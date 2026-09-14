"""Run controller: baseline vs incremental runs, change detection, review items.

This is where SmartSynergy stops being a question-answering pipeline and becomes a watch.
The rules that matter:
  * a run compares against the LAST SUCCESSFUL dossier, not a blank conversation
  * identical evidence must produce zero new alerts, forever
  * a truncated or failed search must NOT advance the watermark
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

from smartsynergy.config import Settings
from smartsynergy.gates import build_profile, final_gate
from smartsynergy.graph import build_graph
from smartsynergy.schemas import (
    ChangeReason,
    Dossier,
    EditorOutput,
    EvidenceClaim,
    Hypothesis,
    ReviewItem,
    Run,
    RunSummary,
    Watch,
    utcnow,
)
from smartsynergy.sources import EuropePMC, PubChem
from smartsynergy.storage import Store
from smartsynergy.tools import RunContext, clear_context, set_context

OVERLAP_DAYS = 7  # re-scan window so late indexing and corrections are not missed


def _short_id() -> str:
    return uuid.uuid4().hex[:10]


_STATE_HEADLINE = {
    "conflicting_evidence": "Evidence now points both ways for this pair",
    "needs_review": "Evidence argues against this pair, or undercuts it",
    "insufficient_evidence": "No verified evidence yet for this pair",
    "preclinical_only": "Support for this pair is preclinical only",
    "indirect_support_only": "Support for this pair is indirect, not in this context",
    "supported_in_scope": "Directly applicable support for this pair",
}


def _state_headline(profile) -> str:  # noqa: ANN001
    """A headline derived only from counted evidence.

    Used whenever the editor's own headline cannot be trusted - it is the most
    prominent string in the inbox, so it must never be free model prose that the
    citation gate has already rejected.
    """
    return _STATE_HEADLINE.get(profile.state, profile.state)


def _build_task(watch: Watch, hyp: Hypothesis, since: str | None, pass_note: str) -> str:
    terms_a = ", ".join(hyp.compound_a_synonyms or [hyp.compound_a])
    terms_b = ", ".join(hyp.compound_b_synonyms or [hyp.compound_b])
    window = (
        f"Only evidence newly indexed or updated since {since} is in scope for this pass."
        if since
        else "This is the baseline pass: the full literature is in scope."
    )
    return (
        f"HYPOTHESIS ({hyp.id}): {hyp.question}\n"
        f"Gene/variant: {watch.gene} {watch.variant}\n"
        f"Disease context: {watch.disease} (also searchable as: {', '.join(watch.disease_synonyms)})\n"
        f"Compound A: {hyp.compound_a} (search terms: {terms_a})\n"
        f"Compound B: {hyp.compound_b} (search terms: {terms_b})\n"
        f"{window}\n{pass_note}\n\n"
        "Evidence from a different cancer type or a different variant is INDIRECT, never direct support."
    )


def _has(claim: EvidenceClaim, marker: str) -> bool:
    return any(marker in ch for ch in claim.challenged_by)


def _detect_change(
    previous: Dossier | None,
    previous_claims: list[EvidenceClaim],
    claims: list[EvidenceClaim],
    new_source_ids: set[str],
) -> tuple[ChangeReason, str] | None:
    """Material change only. Another paper repeating a known finding is not an alert.

    Both NEW claims and REVISED ones are examined. A retraction is the motivating case:
    the paper is not new and its finding text is unchanged, so its claim id is identical.
    Looking only for unseen claim ids would absorb the retraction silently, which is the
    single worst outcome for a tool whose whole promise is to catch exactly that.
    """
    if previous is None:
        return None  # baseline: a dossier is created, but nothing to review yet

    prev_by_id = {c.claim_id: c for c in previous_claims}
    fresh = [c for c in claims if c.claim_id not in prev_by_id]
    revised = [(prev_by_id[c.claim_id], c) for c in claims if c.claim_id in prev_by_id]

    # 1. Something previously relied upon is now unsound. Highest priority.
    for c in fresh:
        if _has(c, "possible_correction"):
            return "source_corrected", f"A tracked source ({c.source_id}) is flagged as corrected or retracted."
    for before, after in revised:
        if _has(after, "possible_correction") and not _has(before, "possible_correction"):
            return "source_corrected", f"A source you are already relying on ({after.source_id}) is now flagged as corrected or retracted."

    # 2. A tracked claim changed direction.
    for before, after in revised:
        if before.label == "supports" and after.label != "supports":
            return "assessment_flipped", f"Evidence previously read as support no longer supports this hypothesis ({after.source_id})."

    # 3. New evidence against.
    for c in fresh:
        if c.label == "opposes" and c.context_match == "direct":
            return "new_counterevidence", f"New directly-applicable evidence argues against this hypothesis ({c.source_id})."

    # 4. Applicability was overstated - whether newly found or newly discovered.
    for before, after in revised:
        if before.context_match == "direct" and after.context_match != "direct":
            return "context_mismatch", f"Evidence previously read as applicable is from a different context ({after.source_id})."
    for c in fresh:
        if c.context_match.startswith("indirect") and _has(c, "context_mismatch"):
            return "context_mismatch", f"Evidence previously read as applicable is from a different context ({c.source_id})."

    # 5. Genuinely new support.
    for c in fresh:
        if c.label == "supports" and c.context_match == "direct" and c.study_design not in ("review", "not_reported"):
            return "new_direct_evidence", f"New directly-applicable supporting evidence ({c.source_id})."
    return None


def run_watch(
    watch_id: str,
    trigger: str = "manual",
    settings: Settings | None = None,
    store: Store | None = None,
    hypothesis_ids: list[str] | None = None,
    verbose: bool = True,
) -> RunSummary:
    from smartsynergy.config import get_settings

    settings = settings or get_settings()
    owns_store = store is None
    store = store or Store(settings.db_path)
    started = time.perf_counter()

    watch = store.get_watch(watch_id)
    if watch is None:
        raise ValueError(f"unknown watch: {watch_id}")

    run = Run(
        id=_short_id(),
        watch_id=watch_id,
        trigger=trigger,  # type: ignore[arg-type]
        source_mode=settings.source_mode,
        model_id=settings.model_id,
    )
    store.start_run(run)

    meta = store.watch_meta(watch_id)
    watermark = meta.get("watermark")
    since = None
    if watermark:
        since = (datetime.fromisoformat(watermark) - timedelta(days=OVERLAP_DAYS)).strftime("%Y-%m-%d")
    run.window_start, run.window_end = since, utcnow().strftime("%Y-%m-%d")

    epmc = EuropePMC(settings.user_agent)
    pubchem = PubChem(settings.user_agent)
    summary = RunSummary(run_id=run.id, watch_id=watch_id, status="running", trigger=trigger)  # type: ignore[arg-type]

    try:
        targets = [h for h in watch.hypotheses if not hypothesis_ids or h.id in hypothesis_ids]
        for hyp in targets:
            if verbose:
                print(f"\n=== {hyp.id}: {hyp.compound_a} + {hyp.compound_b} ===", flush=True)

            # Resolve identities once so literature queries use real synonyms (AMG 510, MRTX849...).
            if not hyp.compound_a_synonyms:
                ident_a = pubchem.resolve(hyp.compound_a)
                hyp.compound_a_synonyms = ident_a.search_terms if ident_a.resolved else [hyp.compound_a]
                hyp.compound_a_cid = ident_a.cid
            if not hyp.compound_b_synonyms:
                ident_b = pubchem.resolve(hyp.compound_b)
                hyp.compound_b_synonyms = ident_b.search_terms if ident_b.resolved else [hyp.compound_b]
                hyp.compound_b_cid = ident_b.cid

            ctx = RunContext(
                watch_id=watch_id,
                run_id=run.id,
                epmc=epmc,
                pubchem=pubchem,
                store=store,
                max_tool_calls=settings.max_tool_calls_per_run,
                max_papers=settings.max_papers_per_run,
            )
            token = set_context(ctx)
            try:
                graph, gate = build_graph(settings, hyp.id)
                task = _build_task(watch, hyp, since, "This is pass 1 of at most 2.")
                result = graph(task, invocation_state={"run_id": run.id, "watch_id": watch_id})
                run.node_events.extend(n.node_id for n in result.execution_order)
                if verbose:
                    print("  nodes:", " -> ".join(n.node_id for n in result.execution_order), flush=True)
            finally:
                clear_context(token)

            gate_result = gate.last_result
            if gate_result is None:
                raise RuntimeError(f"{hyp.id}: evidence gate never ran")

            new_ids = set(store.upsert_sources(watch_id, run.id, ctx.retrieved.values()))
            run.papers_seen += len(ctx.retrieved)
            run.papers_new += len(new_ids)
            run.tool_calls += ctx.tool_calls
            if not ctx.searched:
                # The agent produced an answer without ever running a search. Nothing was
                # examined, so this pass establishes nothing about the window.
                run.coverage = "not_retrieved"
            elif ctx.coverage != "complete":
                run.coverage = ctx.coverage  # type: ignore[assignment]

            previous = store.latest_dossier(hyp.id)

            # The dossier is CUMULATIVE, not a snapshot of one run.
            #
            # An incremental search is date-filtered, so a quiet week legitimately returns
            # nothing and the analyst extracts nothing. Rebuilding the dossier from only
            # this run's output would erase every previously established finding and quietly
            # downgrade the hypothesis to "insufficient evidence" without anything having
            # refuted it.
            #
            # Claims are carried forward UNLESS their source was examined again in this run.
            # A re-examined source yields fresh claims that supersede the old ones, so
            # retractions, critic demotions and corrections still take effect.
            previous_claims = store.get_claims(previous.claim_ids) if previous else []
            refreshed = set(ctx.retrieved)
            carried = [c for c in previous_claims if c.source_id not in refreshed]
            claims = carried + gate_result.accepted

            editor_out = _editor_output(result)
            ok, brief, why = final_gate(editor_out.brief_markdown, claims)
            headline = editor_out.headline
            if not ok:
                if verbose:
                    print(f"  FINAL GATE REJECTED: {why}", flush=True)
                brief = f"_Brief rejected by the citation gate: {why}. Evidence below is unaffected._"
                # The headline is the most prominent text in the inbox and the export.
                # If the brief could not be trusted, its headline cannot be either, so it
                # is replaced by a description derived only from verified evidence.
                headline = ""

            fingerprint = Dossier.compute_fingerprint(claims)

            if previous and previous.fingerprint == fingerprint:
                if verbose:
                    print(f"  no change (fingerprint {fingerprint}); no alert", flush=True)
                summary.dossier_versions[hyp.id] = previous.version
                continue

            profile = build_profile(claims, gate_result.gaps)
            dossier = Dossier(
                id=_short_id(),
                version=(previous.version + 1) if previous else 1,
                watch_id=watch_id,
                hypothesis_id=hyp.id,
                run_id=run.id,
                claim_ids=[c.claim_id for c in claims],
                profile=profile,
                headline=headline or _state_headline(profile),
                brief_markdown=brief,
                coverage=run.coverage,
                coverage_note=f"{len(ctx.retrieved)} sources examined; {ctx.tool_calls} tool calls",
                fingerprint=fingerprint,
            )

            change = _detect_change(previous, previous_claims, claims, new_ids)
            review = None
            if change:
                reason, explanation = change
                review = ReviewItem(
                    id=_short_id(),
                    watch_id=watch_id,
                    hypothesis_id=hyp.id,
                    run_id=run.id,
                    reason=reason,
                    headline=dossier.headline,
                    explanation=explanation,
                    before_version=previous.version if previous else None,
                    after_version=dossier.version,
                    new_source_ids=sorted(new_ids)[:8],
                    fingerprint_after=fingerprint,
                )

            created = store.commit_result(run, dossier, claims, review)
            summary.dossier_versions[hyp.id] = dossier.version
            if created:
                summary.review_items_created += 1
            if verbose:
                state = dossier.profile.state
                print(f"  v{dossier.version} [{state}] claims={len(claims)} alert={'yes' if created else 'no'}", flush=True)

            summary.hypotheses_checked += 1

        # Watermark discipline.
        #
        # An INCREMENTAL pass may only advance the watermark if it covered its whole window.
        # Advancing after a truncated window would permanently skip whatever it did not read,
        # and the next run would report "no changes" - silence that reads as reassurance.
        #
        # A BASELINE pass is different: the literature is effectively unbounded, so it is
        # always truncated by the paper budget and would otherwise never establish a
        # watermark at all, leaving the watch re-running a baseline forever. A baseline is
        # therefore an explicitly bounded snapshot: it sets the watermark, and the dossier
        # records `coverage="truncated"` so the limitation stays visible in the UI.
        #
        # Neither case may advance on `failed` or `not_retrieved`. Those mean the search
        # did not happen or errored, so nothing at all is known about the window; treating
        # that as a snapshot would skip the window on the strength of an outage.
        is_baseline = not watermark
        searched_something = run.coverage in ("complete", "truncated")
        if searched_something and (run.coverage == "complete" or is_baseline):
            store.set_watermark(watch_id, utcnow().isoformat())
        store.touch_checked(watch_id)
        run.status = "completed"

    except Exception as exc:  # noqa: BLE001 - failure must be visible, never "all clear"
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        summary.error = run.error
        if verbose:
            print(f"  RUN FAILED: {run.error}", flush=True)
    finally:
        run.finished_at = utcnow()
        store.finish_run(run)
        epmc.close()
        pubchem.close()
        if owns_store:
            store.close()

    summary.status = run.status
    summary.papers_new = run.papers_new
    summary.coverage = run.coverage
    summary.duration_s = round(time.perf_counter() - started, 1)
    summary.model_calls = run.model_calls
    return summary


def _editor_output(graph_result) -> EditorOutput:  # noqa: ANN001
    node = graph_result.results.get("editor")
    if node is not None:
        for agent_result in node.get_agent_results():
            structured = getattr(agent_result, "structured_output", None)
            if isinstance(structured, EditorOutput):
                return structured
    return EditorOutput(
        brief_markdown="_No brief was produced: the editor did not run (evidence gate blocked it)._",
        headline="Evidence recorded; brief unavailable",
    )
