"""End-to-end test of the watch loop: baseline -> silence -> alert.

This exercises the real controller, the real gate, the real fingerprinting and the real
storage transactions. Only the two external dependencies are substituted: the Bedrock-backed
graph and the HTTP data sources. It is the closest thing to proof that the central promise
holds - *identical evidence never interrupts a human twice*.
"""

from __future__ import annotations

import json

import pytest

from smartsynergy import controller
from smartsynergy.config import Settings
from smartsynergy.schemas import Hypothesis, SourceRecord, Watch
from smartsynergy.storage import Store

ABSTRACT_A = (
    "In KRAS G12C-mutant non-small cell lung cancer, adagrasib combined with cetuximab produced "
    "an objective response rate of 34% in a phase Ib cohort."
)
ABSTRACT_B = (
    "In this randomised phase II trial of KRAS G12C-mutant non-small cell lung cancer, adding "
    "cetuximab to adagrasib did not improve progression-free survival and increased grade 3 rash."
)


class FakeIdentity:
    resolved = True
    cid = 1
    search_terms = ["adagrasib", "MRTX849"]


class FakePubChem:
    def resolve(self, name):  # noqa: ANN001
        return FakeIdentity()

    def close(self):
        pass


class FakeEuropePMC:
    def close(self):
        pass


class FakeGate:
    """Stands in for the gate node object the controller reads results from."""

    def __init__(self, result):
        self.last_result = result


class FakeGraph:
    def __init__(self, sources, claims_json):
        self.sources = sources
        self.claims_json = claims_json

    def __call__(self, task, invocation_state=None):  # noqa: ANN001
        from smartsynergy.tools import get_context

        ctx = get_context()
        for s in self.sources:
            ctx.retrieved[s.source_id] = s
        ctx.tool_calls += 2
        ctx.record_coverage("complete")  # a real agent reaches this only by searching

        from smartsynergy.gates import evidence_gate
        from smartsynergy.schemas import AnalystOutput, CriticOutput

        analyst = AnalystOutput.model_validate(json.loads(self.claims_json))
        result = evidence_gate(
            analyst,
            CriticOutput(verdict="no_issues_found_in_scope"),
            hypothesis_id="kras-egfr",
            run_id="r",
            known_sources=ctx.retrieved,
            followup_used=True,
        )
        self._gate = FakeGate(result)

        class Node:
            node_id = "analyst"

        class Res:
            execution_order = [Node()]
            results: dict = {}

        return Res()


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = Settings(db_path=tmp_path / "w.db", model_id="test-model")
    store = Store(settings.db_path)
    store.save_watch(
        Watch(
            id="w1",
            gene="KRAS",
            variant="G12C",
            disease="non-small cell lung cancer",
            hypotheses=[
                Hypothesis(
                    id="kras-egfr",
                    question="Does adagrasib + cetuximab hold up in NSCLC?",
                    compound_a="adagrasib",
                    compound_b="cetuximab",
                )
            ],
        )
    )
    monkeypatch.setattr(controller, "PubChem", lambda ua: FakePubChem())
    monkeypatch.setattr(controller, "EuropePMC", lambda ua: FakeEuropePMC())
    yield settings, store, monkeypatch
    store.close()


def source(sid: str, abstract: str) -> SourceRecord:
    return SourceRecord(
        source_id=sid,
        pmid=sid,
        title=f"Study {sid}",
        url=f"https://europepmc.org/article/MED/{sid}",
        abstract=abstract,
        access_level="abstract_only",
    )


def analyst_json(*claims: dict) -> str:
    return json.dumps({"claims": list(claims), "gaps": [], "searched_queries": ["q"]})


SUPPORT = {
    "source_id": "111",
    "finding": "The combination produced a 34% response rate in this setting.",
    "supporting_passage": "an objective response rate of 34% in a phase Ib cohort",
    "label": "supports",
    "context_match": "direct",
    "study_design": "interventional",
}
OPPOSE = {
    "source_id": "222",
    "finding": "A randomised trial found no PFS benefit and more toxicity.",
    "supporting_passage": "did not improve progression-free survival and increased grade 3 rash",
    "label": "opposes",
    "context_match": "direct",
    "study_design": "interventional",
}



def install_graph(monkeypatch, sources, claims_json):
    """Point the controller at a stand-in graph that drives the REAL evidence gate."""
    holder = FakeGate(None)
    graph = FakeGraph(sources, claims_json)

    def fake_build(settings_, hyp_id):
        def run(task, invocation_state=None):
            res = graph(task, invocation_state)
            holder.last_result = graph._gate.last_result
            return res

        return run, holder

    monkeypatch.setattr(controller, "build_graph", fake_build)


def test_baseline_then_silence_then_alert(env):
    settings, store, monkeypatch = env
    s1 = source("111", ABSTRACT_A)
    s2 = source("222", ABSTRACT_B)

    # --- Run 1: baseline. A dossier is created; nobody is interrupted. -------------
    install_graph(monkeypatch, [s1], analyst_json(SUPPORT))
    summary = controller.run_watch("w1", settings=settings, store=store, verbose=False)
    assert summary.status == "completed", summary.error
    assert summary.papers_new == 1
    assert summary.review_items_created == 0, "a baseline run must not interrupt anyone"
    assert store.latest_dossier("kras-egfr").version == 1

    # --- Run 2: the literature has not moved. Absolute silence. --------------------
    install_graph(monkeypatch, [s1], analyst_json(SUPPORT))
    summary = controller.run_watch("w1", settings=settings, store=store, verbose=False)
    assert summary.review_items_created == 0, "identical evidence must never produce an alert"
    assert summary.papers_new == 0, "an already-seen source is not new"
    assert store.latest_dossier("kras-egfr").version == 1, "no change means no new version"

    # --- Run 3: a randomised trial contradicts the hypothesis. Interrupt. ----------
    install_graph(monkeypatch, [s1, s2], analyst_json(SUPPORT, OPPOSE))
    summary = controller.run_watch("w1", settings=settings, store=store, verbose=False)
    assert summary.review_items_created == 1, "new direct counterevidence must reach the human"
    assert summary.papers_new == 1

    dossier = store.latest_dossier("kras-egfr")
    assert dossier.version == 2
    assert dossier.profile.state == "conflicting_evidence"

    inbox = store.list_inbox("w1")
    assert len(inbox) == 1
    assert inbox[0].reason == "new_counterevidence"
    assert inbox[0].urgency >= 2

    # --- Run 4: still the same evidence. The alert is not repeated. ---------------
    install_graph(monkeypatch, [s1, s2], analyst_json(SUPPORT, OPPOSE))
    summary = controller.run_watch("w1", settings=settings, store=store, verbose=False)
    assert summary.review_items_created == 0
    assert len(store.list_inbox("w1")) == 1, "the same change must never alert twice"

    runs = store.list_runs("w1")
    assert len(runs) == 4 and all(r.status == "completed" for r in runs)


def test_a_failed_run_does_not_advance_the_watermark(env):
    """Silence after a failure would read as 'all clear'. It must not."""
    settings, store, monkeypatch = env

    def exploding_build(settings_, hyp_id):
        def run(task, invocation_state=None):
            raise RuntimeError("bedrock unavailable")

        return run, FakeGate(None)

    monkeypatch.setattr(controller, "build_graph", exploding_build)
    summary = controller.run_watch("w1", settings=settings, store=store, verbose=False)

    assert summary.status == "failed"
    assert "bedrock unavailable" in summary.error
    assert store.watch_meta("w1").get("watermark") in (None, "")
    assert store.list_runs("w1")[0].status == "failed"


def test_every_decision_the_ui_offers_can_actually_be_recorded(env):
    """The UI and CLI offer acknowledged/dismissed/acted_on; all three must validate."""
    settings, store, monkeypatch = env
    install_graph(monkeypatch, [source("111", ABSTRACT_A)], analyst_json(SUPPORT))
    controller.run_watch("w1", settings=settings, store=store, verbose=False)
    install_graph(monkeypatch, [source("111", ABSTRACT_A), source("222", ABSTRACT_B)], analyst_json(SUPPORT, OPPOSE))
    controller.run_watch("w1", settings=settings, store=store, verbose=False)

    item = store.list_inbox("w1")[0]
    for state in ("acknowledged", "dismissed", "acted_on"):
        store.record_decision(item.id, state, note=f"set to {state}")
        assert store.list_inbox("w1", state=state)[0].id == item.id
    assert store.list_inbox("w1") == [], "a decided item leaves the open inbox"


def test_counterevidence_outranks_promising_news_in_the_inbox():
    from smartsynergy.schemas import ReviewItem

    def item(reason):
        return ReviewItem(id=reason, watch_id="w", hypothesis_id="h", run_id="r",
                          reason=reason, headline="", explanation="")

    order = sorted(
        [item("new_direct_evidence"), item("new_counterevidence"), item("context_mismatch"), item("source_corrected")],
        key=lambda i: -i.urgency,
    )
    assert [i.reason for i in order] == [
        "new_counterevidence", "source_corrected", "context_mismatch", "new_direct_evidence",
    ]


class TruncatingEPMC:
    """Search that always reports partial coverage, like a real broad query would."""

    def __init__(self, records):
        self.records = records

    def search(self, query, max_records=8, **kw):  # noqa: ANN001
        from smartsynergy.sources.base import SearchResult

        return SearchResult(records=self.records, coverage="truncated", total_hits=9999)

    def close(self):
        pass


def install_truncating(monkeypatch, sources, claims_json):
    install_graph(monkeypatch, sources, claims_json)
    graph_factory = controller.build_graph

    def build(settings_, hyp_id):
        run, gate = graph_factory(settings_, hyp_id)

        def wrapped(task, invocation_state=None):
            from smartsynergy.tools import get_context

            result = run(task, invocation_state)
            get_context().record_coverage("truncated")
            return result

        return wrapped, gate

    monkeypatch.setattr(controller, "build_graph", build)


def test_a_baseline_sets_the_watermark_even_though_it_is_bounded(env):
    """Otherwise the watch re-runs a baseline forever and never tracks changes."""
    settings, store, monkeypatch = env
    install_truncating(monkeypatch, [source("111", ABSTRACT_A)], analyst_json(SUPPORT))
    summary = controller.run_watch("w1", settings=settings, store=store, verbose=False)

    assert summary.status == "completed"
    assert summary.coverage == "truncated"
    assert store.watch_meta("w1").get("watermark"), "a baseline must establish a watermark"
    assert store.latest_dossier("kras-egfr").coverage == "truncated", (
        "the bound must stay visible on the dossier"
    )


def test_a_truncated_incremental_pass_does_not_advance_the_watermark(env):
    """Skipping part of a window and then reporting silence is the worst failure mode."""
    settings, store, monkeypatch = env
    install_graph(monkeypatch, [source("111", ABSTRACT_A)], analyst_json(SUPPORT))
    controller.run_watch("w1", settings=settings, store=store, verbose=False)
    first = store.watch_meta("w1").get("watermark")
    assert first

    install_truncating(monkeypatch, [source("222", ABSTRACT_B)], analyst_json(OPPOSE))
    summary = controller.run_watch("w1", settings=settings, store=store, verbose=False)

    assert summary.coverage == "truncated"
    assert store.watch_meta("w1").get("watermark") == first, (
        "an incomplete incremental window must be re-scanned, not skipped"
    )


class SilentGraph(FakeGraph):
    """An agent that answers without ever searching. It establishes nothing.

    Identical to FakeGraph except that it never calls `record_coverage`, because no
    search tool was ever invoked.
    """

    def __init__(self):
        super().__init__([], json.dumps({"claims": [], "gaps": []}))

    def __call__(self, task, invocation_state=None):  # noqa: ANN001
        from smartsynergy.gates import evidence_gate
        from smartsynergy.schemas import AnalystOutput, CriticOutput

        result = evidence_gate(
            AnalystOutput(claims=[], gaps=[]),
            CriticOutput(verdict="no_issues_found_in_scope"),
            hypothesis_id="kras-egfr",
            run_id="r",
            known_sources={},
            followup_used=True,
        )
        self._gate = FakeGate(result)

        class Node:
            node_id = "analyst"

        class Res:
            execution_order = [Node()]
            results: dict = {}

        return Res()


def test_a_run_that_never_searched_does_not_set_a_watermark(env):
    """Otherwise an empty model reply silently marks the whole window as checked."""
    settings, store, monkeypatch = env
    holder = FakeGate(None)
    graph = SilentGraph()

    def fake_build(settings_, hyp_id):
        def run(task, invocation_state=None):
            res = graph(task, invocation_state)
            holder.last_result = graph._gate.last_result
            return res

        return run, holder

    monkeypatch.setattr(controller, "build_graph", fake_build)

    summary = controller.run_watch("w1", settings=settings, store=store, verbose=False)

    assert summary.coverage == "not_retrieved"
    assert not store.watch_meta("w1").get("watermark"), (
        "a pass that searched nothing must not claim to have covered the window"
    )


def test_budget_exhaustion_is_reported_to_the_run_not_just_the_model(env):
    """The tool tells the model 'truncated'; the run has to hear it too."""
    from smartsynergy.tools import RunContext, clear_context, search_literature, set_context

    ctx = RunContext(watch_id="w1", run_id="r1", max_tool_calls=0)
    token = set_context(ctx)
    try:
        out = search_literature("kras g12c", max_results=5)
    finally:
        clear_context(token)

    assert "budget_exhausted" in out
    assert ctx.coverage == "truncated", "budget exhaustion must degrade run coverage"


def test_coverage_only_ever_gets_worse_within_a_run():
    """A later good search does not undo an earlier missed one."""
    from smartsynergy.tools import RunContext

    ctx = RunContext(watch_id="w", run_id="r")
    assert ctx.coverage == "not_retrieved"
    ctx.record_coverage("complete")
    assert ctx.coverage == "complete"
    ctx.record_coverage("truncated")
    assert ctx.coverage == "truncated"
    ctx.record_coverage("complete")
    assert ctx.coverage == "truncated", "coverage must not recover"
    ctx.record_coverage("failed")
    assert ctx.coverage == "failed"
