"""Tests for the deterministic core: the parts that must be correct without a model."""

from __future__ import annotations

import pytest

from smartsynergy.controller import _detect_change
from smartsynergy.gates import build_profile, evidence_gate, final_gate
from smartsynergy.graph import parse_upstream
from smartsynergy.schemas import (
    AnalystOutput,
    Challenge,
    CriticOutput,
    Dossier,
    EvidenceClaimDraft,
    Hypothesis,
    ReviewItem,
    Run,
    SourceRecord,
    Watch,
)
from smartsynergy.storage import RunInProgress, Store

PASSAGE = "Sotorasib plus pembrolizumab produced grade 3 hepatotoxicity in 8 of 20 patients."
ABSTRACT = "Background. " + PASSAGE + " We therefore recommend caution."


def src(source_id: str = "38000001", **kw) -> SourceRecord:
    base = dict(
        source_id=source_id,
        pmid=source_id,
        title="A study",
        url=f"https://europepmc.org/article/MED/{source_id}",
        abstract=ABSTRACT,
        access_level="abstract_only",
    )
    base.update(kw)
    return SourceRecord(**base)


def draft(source_id: str = "38000001", **kw) -> EvidenceClaimDraft:
    base = dict(
        source_id=source_id,
        finding="The combination caused dose-limiting liver toxicity.",
        supporting_passage=PASSAGE,
        label="opposes",
        context_match="direct",
        study_design="interventional",
    )
    base.update(kw)
    return EvidenceClaimDraft(**base)


def run_gate(analyst, critic, known, followup_used=False):
    return evidence_gate(
        analyst, critic, hypothesis_id="kras-pd1", run_id="r1", known_sources=known, followup_used=followup_used
    )


# --- the gate is the trust boundary -------------------------------------------------


def test_rejects_claims_about_sources_that_were_never_retrieved():
    """A hallucinated PMID is the single most dangerous failure mode. It must be impossible."""
    result = run_gate(AnalystOutput(claims=[draft("99999999")]), CriticOutput(verdict="no_issues_found_in_scope"), {})
    assert result.accepted == []
    assert result.rejected and "not retrieved" in result.rejected[0][1].lower()


def test_verbatim_quote_must_appear_in_the_retrieved_text():
    known = {"38000001": src()}
    good = run_gate(AnalystOutput(claims=[draft()]), CriticOutput(verdict="no_issues_found_in_scope"), known)
    assert good.accepted[0].passage_verified is True

    paraphrased = draft(supporting_passage="Patients experienced severe liver problems on the combination.")
    bad = run_gate(AnalystOutput(claims=[paraphrased]), CriticOutput(verdict="no_issues_found_in_scope"), known)
    assert bad.accepted[0].passage_verified is False


def test_quote_matching_ignores_whitespace_and_case_but_not_content():
    known = {"38000001": src()}
    messy = draft(supporting_passage="  sotorasib   PLUS pembrolizumab produced GRADE 3 hepatotoxicity in 8 of 20 patients. ")
    result = run_gate(AnalystOutput(claims=[messy]), CriticOutput(verdict="no_issues_found_in_scope"), known)
    assert result.accepted[0].passage_verified is True


def test_critic_context_mismatch_demotes_direct_evidence():
    """The headline defence against the 'it worked in colorectal so it works here' error."""
    known = {"38000001": src()}
    critic = CriticOutput(
        verdict="issues_found",
        challenges=[Challenge(target_source_id="38000001", issue="context_mismatch", explanation="Colorectal cohort.")],
    )
    result = run_gate(AnalystOutput(claims=[draft(context_match="direct")]), critic, known)
    claim = result.accepted[0]
    assert claim.context_match == "indirect_other_tumor"
    assert any("context_mismatch" in c for c in claim.challenged_by)


def test_correction_flag_downgrades_support_to_unclear():
    known = {"38000001": src(is_corrected=True)}
    critic = CriticOutput(
        verdict="issues_found",
        challenges=[Challenge(target_source_id="38000001", issue="possible_correction", explanation="Erratum indexed.")],
    )
    result = run_gate(AnalystOutput(claims=[draft(label="supports")]), critic, known)
    assert result.accepted[0].label == "unclear"


def test_followup_is_offered_once_and_only_once():
    known = {"38000001": src()}
    critic = CriticOutput(verdict="issues_found", followup_queries=['"sotorasib" AND "pembrolizumab"'])
    first = run_gate(AnalystOutput(claims=[draft()]), critic, known, followup_used=False)
    assert first.needs_followup is True and first.marker == "FOLLOWUP_REQUIRED"

    second = run_gate(AnalystOutput(claims=[draft()]), critic, known, followup_used=True)
    assert second.needs_followup is False and second.marker == "GATE_PASSED"


def test_final_gate_rejects_a_brief_citing_a_claim_that_does_not_exist():
    known = {"38000001": src()}
    claims = run_gate(AnalystOutput(claims=[draft()]), CriticOutput(verdict="no_issues_found_in_scope"), known).accepted
    ok, _, why = final_gate("Evidence shows harm [claim:totally-made-up].", claims)
    assert ok is False and "made-up" in why or not ok

    ok2, _, _ = final_gate(f"Evidence shows harm [claim:{claims[0].claim_id}].", claims)
    assert ok2 is True


# --- the profile refuses to average incomparable things -----------------------------


def test_conflicting_evidence_is_reported_as_conflict_not_averaged_away():
    known = {"a1": src("a1"), "b2": src("b2")}
    analyst = AnalystOutput(
        claims=[
            draft("a1", label="supports", finding="Tumour regression observed."),
            draft("b2", label="opposes", finding="No benefit and added toxicity."),
        ]
    )
    claims = run_gate(analyst, CriticOutput(verdict="no_issues_found_in_scope"), known).accepted
    profile = build_profile(claims, [])
    assert profile.direct_support == 1 and profile.opposing == 1
    assert profile.state == "conflicting_evidence"


def test_preclinical_only_evidence_never_looks_like_clinical_support():
    known = {"a1": src("a1")}
    analyst = AnalystOutput(claims=[draft("a1", label="supports", context_match="direct", study_design="cell_line")])
    claims = run_gate(analyst, CriticOutput(verdict="no_issues_found_in_scope"), known).accepted
    assert build_profile(claims, []).state == "preclinical_only"


# --- fingerprints decide whether a human gets interrupted ---------------------------


def test_fingerprint_is_independent_of_order_and_prose():
    known = {"a1": src("a1"), "b2": src("b2")}
    a = run_gate(AnalystOutput(claims=[draft("a1"), draft("b2")]), CriticOutput(verdict="no_issues_found_in_scope"), known)
    b = run_gate(AnalystOutput(claims=[draft("b2"), draft("a1")]), CriticOutput(verdict="no_issues_found_in_scope"), known)
    assert Dossier.compute_fingerprint(a.accepted) == Dossier.compute_fingerprint(b.accepted)


def test_change_detection_stays_silent_when_nothing_material_changed():
    known = {"a1": src("a1")}
    claims = run_gate(AnalystOutput(claims=[draft("a1")]), CriticOutput(verdict="no_issues_found_in_scope"), known).accepted
    previous = Dossier(
        id="d1", version=1, watch_id="w", hypothesis_id="h", run_id="r0",
        claim_ids=[c.claim_id for c in claims], fingerprint=Dossier.compute_fingerprint(claims),
    )
    assert _detect_change(previous, claims, claims, set()) is None


def test_new_directly_applicable_counterevidence_raises_an_alert():
    known = {"a1": src("a1"), "b2": src("b2")}
    old = run_gate(AnalystOutput(claims=[draft("a1", label="supports")]), CriticOutput(verdict="no_issues_found_in_scope"), known).accepted
    new = run_gate(
        AnalystOutput(claims=[draft("a1", label="supports"), draft("b2", label="opposes")]),
        CriticOutput(verdict="no_issues_found_in_scope"), known,
    ).accepted
    previous = Dossier(
        id="d1", version=1, watch_id="w", hypothesis_id="h", run_id="r0",
        claim_ids=[c.claim_id for c in old], fingerprint=Dossier.compute_fingerprint(old),
    )
    change = _detect_change(previous, old, new, {"b2"})
    assert change is not None and change[0] == "new_counterevidence"


def test_baseline_run_creates_no_alerts():
    known = {"a1": src("a1")}
    claims = run_gate(AnalystOutput(claims=[draft("a1")]), CriticOutput(verdict="no_issues_found_in_scope"), known).accepted
    assert _detect_change(None, [], claims, {"a1"}) is None


# --- storage: the guarantees a scheduler depends on ---------------------------------


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.save_watch(
        Watch(
            id="w1", gene="KRAS", variant="G12C", disease="NSCLC",
            hypotheses=[Hypothesis(id="h1", question="Q?", compound_a="A", compound_b="B")],
        )
    )
    yield s
    s.close()


def test_two_runs_of_the_same_watch_cannot_overlap(store):
    store.start_run(Run(id="r1", watch_id="w1", trigger="scheduled"))
    with pytest.raises(RunInProgress):
        store.start_run(Run(id="r2", watch_id="w1", trigger="manual"))


def test_the_same_change_never_alerts_twice(store):
    run = Run(id="r1", watch_id="w1", trigger="manual")
    store.start_run(run)
    known = {"a1": src("a1")}
    claims = run_gate(AnalystOutput(claims=[draft("a1")]), CriticOutput(verdict="no_issues_found_in_scope"), known).accepted
    fp = Dossier.compute_fingerprint(claims)
    dossier = Dossier(id="d1", version=1, watch_id="w1", hypothesis_id="h1", run_id="r1", claim_ids=[c.claim_id for c in claims], fingerprint=fp)
    review = ReviewItem(id="i1", watch_id="w1", hypothesis_id="h1", run_id="r1", reason="new_direct_evidence", headline="x", explanation="y", after_version=1, fingerprint_after=fp)

    assert store.commit_result(run, dossier, claims, review) is True
    duplicate = review.model_copy(update={"id": "i2"})
    assert store.commit_result(run, dossier, claims, duplicate) is False
    assert len(store.list_inbox("w1")) == 1


def test_evidence_survives_a_round_trip_through_the_database(store):
    run = Run(id="r1", watch_id="w1", trigger="manual")
    store.start_run(run)
    store.upsert_sources("w1", "r1", [src("a1")])
    claims = run_gate(AnalystOutput(claims=[draft("a1")]), CriticOutput(verdict="no_issues_found_in_scope"), {"a1": src("a1")}).accepted
    store.commit_result(run, None, claims, None)

    loaded = store.get_claims([c.claim_id for c in claims])
    assert loaded[0].supporting_passage == PASSAGE
    assert loaded[0].passage_verified is True
    assert store.get_source("a1").title == "A study"


def test_a_source_seen_before_is_not_reported_as_new(store):
    first = store.upsert_sources("w1", "r1", [src("a1"), src("b2")])
    second = store.upsert_sources("w1", "r2", [src("a1"), src("b2"), src("c3")])
    assert set(first) == {"a1", "b2"}
    assert second == ["c3"]


# --- graph plumbing ------------------------------------------------------------------


def test_structured_outputs_are_recovered_from_concatenated_node_text():
    text = (
        "From analyst:\n"
        '{"claims": [{"source_id": "a1", "finding": "f", "supporting_passage": "p", "label": "supports",'
        ' "context_match": "direct"}], "gaps": ["no trial"], "searched_queries": ["q1"]}\n'
        'From critic:\n{"verdict": "issues_found", "challenges": [], "followup_queries": ["q2"]}'
    )
    analyst, critic = parse_upstream(text)
    assert len(analyst.claims) == 1 and analyst.gaps == ["no trial"]
    assert critic.verdict == "issues_found" and critic.followup_queries == ["q2"]


def test_malformed_model_output_degrades_instead_of_crashing():
    analyst, critic = parse_upstream("the model replied in prose, with a stray { brace")
    assert analyst.claims == [] and critic.verdict == "no_issues_found_in_scope"


# --- CLI output: the operator has to be able to read it -----------------------------


def test_run_table_columns_never_collide_for_a_long_watch_id(store, tmp_path, capsys, monkeypatch):
    """A fixed column width runs the watch id straight into the status column."""
    import argparse
    import contextlib

    from smartsynergy import worker
    from smartsynergy.schemas import Run

    long_id = "a-watch-id-that-is-much-longer-than-any-guess"
    store.save_watch(
        Watch(
            id=long_id, gene="KRAS", variant="G12C", disease="NSCLC",
            hypotheses=[Hypothesis(id="h2", question="Q?", compound_a="A", compound_b="B")],
        )
    )
    run = Run(id="r9", watch_id=long_id, trigger="manual")
    store.start_run(run)
    run.status = "completed"
    store.finish_run(run)

    monkeypatch.setattr(worker, "_store", lambda: contextlib.nullcontext(store))
    worker.cmd_list_runs(argparse.Namespace(watch_id=None, limit=10))

    header, *rows = capsys.readouterr().out.strip().splitlines()
    assert long_id in "".join(rows)
    status_col = header.index("STATUS")
    for row in rows:
        assert row[status_col - 1] == " ", f"columns collided: {row!r}"
        assert row[status_col:].startswith(("completed", "running", "failed")), (
            f"status not aligned under its header: {row!r}"
        )
