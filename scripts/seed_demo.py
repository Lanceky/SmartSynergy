"""Seed a demonstration database using REAL literature and a scripted model.

Bedrock credentials are not always available (and are not needed to show that the *watch*
works). This script runs the real controller, the real Europe PMC adapter, the real evidence
gate, the real fingerprinting and the real storage layer. Only the model's judgment is
scripted - every source, title, abstract and quoted passage below is fetched live and is
genuinely verified against the retrieved text by the gate.

    python scripts/seed_demo.py

It plays three consecutive days:
    day 1  baseline            -> a dossier is created, nobody is interrupted
    day 2  nothing new         -> total silence
    day 3  contradicting paper -> one item reaches the inbox
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smartsynergy import controller  # noqa: E402
from smartsynergy.config import get_settings  # noqa: E402
from smartsynergy.gates import _claim_id  # noqa: E402
from smartsynergy.schemas import (  # noqa: E402
    AnalystOutput,
    Challenge,
    CriticOutput,
    EditorOutput,
    EvidenceClaimDraft,
    SourceRecord,
    Watch,
)
from smartsynergy.sources import EuropePMC  # noqa: E402
from smartsynergy.storage import Store  # noqa: E402
from smartsynergy.testing import ScriptedModel  # noqa: E402

QUERIES = {
    "support": '(KRAS AND "G12C") AND (adagrasib OR "MRTX849") AND cetuximab AND colorectal',
    "counter": '(KRAS AND "G12C") AND (sotorasib OR "AMG 510") AND ("non-small cell lung" OR NSCLC) AND resistance',
}
SENTENCE = re.compile(r"(?<=[.!?])\s+")
REAL_BUILD_GRAPH = controller.build_graph  # captured once; install() must not nest wrappers


def first_sentence_mentioning(record: SourceRecord, *words: str) -> str | None:
    """Pick a real sentence from a real abstract - the gate will verify it verbatim."""
    for sentence in SENTENCE.split(record.abstract or ""):
        low = sentence.lower()
        if all(w.lower() in low for w in words) and 40 < len(sentence) < 600:
            return sentence.strip()
    return None


def pick(records: list[SourceRecord], *words: str) -> tuple[SourceRecord, str] | None:
    for rec in records:
        passage = first_sentence_mentioning(rec, *words)
        if passage:
            return rec, passage
    return None


def install(monkey_sources: list[SourceRecord], analyst: AnalystOutput, critic: CriticOutput, editor: EditorOutput):
    """Point the controller at a scripted model and a fixed set of already-fetched sources."""
    from smartsynergy.sources.base import SearchResult

    class SeededEPMC:
        def search(self, query, max_records=8, **kw):  # noqa: ANN001
            return SearchResult(records=monkey_sources[:max_records], coverage="complete",
                                total_hits=len(monkey_sources))

        def close(self):
            pass

    class SeededPubChem:
        def resolve(self, name):  # noqa: ANN001
            class I:
                resolved = False
                cid = None
                search_terms: list[str] = []

            return I()

        def close(self):
            pass

    # The scripted model really calls search_literature, so the gate sees genuinely
    # retrieved sources - a claim about an un-retrieved source would be deleted.
    model = ScriptedModel(
        [analyst],
        critic,
        editor,
        tool_calls=[("search_literature", {"query": QUERIES["support"], "max_results": 8})],
    )
    controller.EuropePMC = lambda ua: SeededEPMC()
    controller.PubChem = lambda ua: SeededPubChem()
    controller.build_graph = lambda s, h: REAL_BUILD_GRAPH(s, h, model=model)
    return model


def main() -> int:
    settings = get_settings()
    print(f"database: {settings.db_path}")
    epmc = EuropePMC(settings.user_agent)

    print("\nfetching real literature from Europe PMC ...")
    support_hits = epmc.search(QUERIES["support"], max_records=25).records
    counter_hits = epmc.search(QUERIES["counter"], max_records=25).records
    print(f"  {len(support_hits)} records for the supporting query")
    print(f"  {len(counter_hits)} records for the resistance query")

    support = (
        pick(support_hits, "cetuximab", "kras")
        or pick(support_hits, "cetuximab")
        or pick(support_hits, "adagrasib")
        or pick(support_hits, "colorectal", "g12c")
    )
    counter = (
        pick(counter_hits, "resistance", "sotorasib")
        or pick(counter_hits, "resistance", "g12c")
        or pick(counter_hits, "resistance")
    )
    epmc.close()

    if not support or not counter:
        print("\ncould not find usable passages in the live results; try again later")
        return 1

    s_rec, s_passage = support
    c_rec, c_passage = counter
    print(f"\nsupporting source : {s_rec.source_id}  {s_rec.title[:70]}")
    print(f"  quoted passage  : {s_passage[:100]}...")
    print(f"opposing source   : {c_rec.source_id}  {c_rec.title[:70]}")
    print(f"  quoted passage  : {c_passage[:100]}...")

    store = Store(settings.db_path)
    watch = Watch.model_validate_json(Path("fixtures/panel.json").read_text())
    store.save_watch(watch)

    supporting_claim = EvidenceClaimDraft(
        source_id=s_rec.source_id,
        finding="Reported activity for this drug pair, but in colorectal cancer rather than lung.",
        supporting_passage=s_passage,
        label="supports",
        context_match="direct",
        study_design="interventional",
        comparator="single-agent KRAS G12C inhibitor",
        outcome="objective response rate",
        limitations="Cohort is colorectal, not non-small cell lung cancer.",
    )
    opposing_claim = EvidenceClaimDraft(
        source_id=c_rec.source_id,
        finding="Reports a resistance mechanism that undercuts durable benefit from this strategy.",
        supporting_passage=c_passage,
        label="opposes",
        context_match="direct",
        study_design="observational",
        comparator="not_reported",
        outcome="acquired resistance",
        limitations="Reported in a limited patient series.",
    )

    # Claim IDs are derived deterministically by the gate, so the brief can cite them
    # up front - and the final citation gate will reject it if any ID is wrong.
    support_id = _claim_id("kras-egfr", supporting_claim.source_id, supporting_claim.finding)
    oppose_id = _claim_id("kras-egfr", opposing_claim.source_id, opposing_claim.finding)

    editor = EditorOutput(
        brief_markdown=(
            "The strongest evidence for this pair is from **colorectal cancer**, not from "
            f"KRAS G12C non-small cell lung cancer [claim:{support_id}]. A separate report "
            f"describes a resistance route that undercuts durable benefit [claim:{oppose_id}]. "
            "Treat this hypothesis as unsupported *in lung* until an NSCLC cohort reports."
        ),
        cited_claim_ids=[support_id, oppose_id],
        headline="Support for this pair is weaker than it looks: the strongest evidence is colorectal, not lung",
    )

    print("\n--- day 1: baseline -------------------------------------------------")
    install([s_rec], AnalystOutput(claims=[supporting_claim], gaps=["No NSCLC cohort has reported."]),
            CriticOutput(verdict="no_issues_found_in_scope"), editor)
    r1 = controller.run_watch("kras-g12c-nsclc", settings=settings, store=store,
                              hypothesis_ids=["kras-egfr"], verbose=False)
    print(f"  {r1.error or ''}") if r1.error else None
    print(f"  {r1.status}: {r1.papers_new} new source(s), {r1.review_items_created} alert(s) -> a baseline never interrupts")

    print("\n--- day 2: the literature has not moved -----------------------------")
    install([s_rec], AnalystOutput(claims=[supporting_claim], gaps=["No NSCLC cohort has reported."]),
            CriticOutput(verdict="no_issues_found_in_scope"), editor)
    r2 = controller.run_watch("kras-g12c-nsclc", settings=settings, store=store,
                              hypothesis_ids=["kras-egfr"], verbose=False)
    print(f"  {r2.error or ''}") if r2.error else None
    print(f"  {r2.status}: {r2.papers_new} new source(s), {r2.review_items_created} alert(s) -> silence, by design")

    print("\n--- day 3: a contradicting paper is indexed -------------------------")
    install(
        [s_rec, c_rec],
        AnalystOutput(claims=[supporting_claim, opposing_claim], gaps=["No NSCLC cohort has reported."]),
        CriticOutput(
            verdict="issues_found",
            challenges=[
                Challenge(
                    target_source_id=s_rec.source_id,
                    issue="context_mismatch",
                    explanation="This evidence is from colorectal cancer, not KRAS G12C NSCLC.",
                )
            ],
        ),
        editor,
    )
    r3 = controller.run_watch("kras-g12c-nsclc", settings=settings, store=store,
                              hypothesis_ids=["kras-egfr"], verbose=False)
    print(f"  {r3.error or ''}") if r3.error else None
    print(f"  {r3.status}: {r3.papers_new} new source(s), {r3.review_items_created} alert(s) -> you are interrupted")

    dossier = store.latest_dossier("kras-egfr")
    print("\n--- result ----------------------------------------------------------")
    print(f"  dossier version : v{dossier.version}")
    print(f"  assessment      : {dossier.profile.state}")
    print(f"  direct support  : {dossier.profile.direct_support}")
    print(f"  indirect        : {dossier.profile.indirect_support}  <- demoted by the critic")
    print(f"  opposing        : {dossier.profile.opposing}")
    for item in store.list_inbox("kras-g12c-nsclc"):
        print(f"\n  INBOX  [{item.reason}] {item.headline}")
        print(f"         {item.explanation}")
    store.close()

    print("\nopen the console:  .venv/bin/python -m streamlit run app/streamlit_app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
