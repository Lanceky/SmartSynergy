"""Runs the REAL Strands graph end to end with a scripted model provider.

Without AWS credentials the Bedrock call itself cannot be exercised, but everything around it
can be, and that is where the interesting failure modes live: the AND-join into the gate,
structured-output recovery from concatenated node text, conditional routing, the bounded
follow-up loop, and tool dispatch from inside a graph node.
"""

from __future__ import annotations

import pytest

from smartsynergy.config import Settings
from smartsynergy.controller import _editor_output
from smartsynergy.graph import build_graph
from smartsynergy.schemas import (
    AnalystOutput,
    Challenge,
    CriticOutput,
    EditorOutput,
    EvidenceClaimDraft,
    SourceRecord,
)
from smartsynergy.testing import ScriptedModel
from smartsynergy.tools import RunContext, clear_context, set_context

PASSAGE = "adagrasib plus cetuximab achieved a 34% objective response rate"
ABSTRACT = f"In KRAS G12C colorectal cancer, {PASSAGE} in a phase Ib cohort."


def make_source(sid: str = "111") -> SourceRecord:
    return SourceRecord(
        source_id=sid,
        pmid=sid,
        title=f"Study {sid}",
        url=f"https://europepmc.org/article/MED/{sid}",
        abstract=ABSTRACT,
        access_level="abstract_only",
    )


def make_claim(sid: str = "111", **kw) -> EvidenceClaimDraft:
    base = dict(
        source_id=sid,
        finding="The pair showed a 34% response rate.",
        supporting_passage=PASSAGE,
        label="supports",
        context_match="direct",
        study_design="interventional",
    )
    base.update(kw)
    return EvidenceClaimDraft(**base)


@pytest.fixture
def ctx():
    context = RunContext(watch_id="w1", run_id="r1")
    context.retrieved["111"] = make_source("111")
    context.retrieved["222"] = make_source("222")
    token = set_context(context)
    yield context
    clear_context(token)


def run_graph(model, hypothesis_id="kras-egfr"):
    graph, gate = build_graph(Settings(model_id="scripted"), hypothesis_id, model=model)
    result = graph("Does adagrasib + cetuximab hold up in NSCLC?")
    return result, gate


def test_the_whole_graph_executes_in_the_intended_order(ctx):
    model = ScriptedModel(
        [AnalystOutput(claims=[make_claim()], searched_queries=["q"])],
        CriticOutput(verdict="no_issues_found_in_scope"),
        EditorOutput(brief_markdown="A brief.", headline="Evidence is limited but positive."),
    )
    result, gate = run_graph(model)
    path = [n.node_id for n in result.execution_order]

    assert path == ["analyst", "critic", "gate", "editor"]
    assert gate.last_result is not None
    assert len(gate.last_result.accepted) == 1, "the gate must have received the analyst's claims"


def test_the_gate_receives_both_upstream_outputs_not_just_one(ctx):
    """Strands graphs fire on ANY incoming edge; the AND-join is what makes the critic count."""
    model = ScriptedModel(
        [AnalystOutput(claims=[make_claim(context_match="direct")], searched_queries=["q"])],
        CriticOutput(
            verdict="issues_found",
            challenges=[
                Challenge(
                    target_source_id="111",
                    issue="context_mismatch",
                    explanation="This cohort is colorectal, not lung.",
                )
            ],
        ),
        EditorOutput(brief_markdown="A brief."),
    )
    _, gate = run_graph(model)

    claim = gate.last_result.accepted[0]
    assert claim.context_match == "indirect_other_tumor", (
        "the critic's challenge reached the gate, so direct support was demoted"
    )


def test_the_follow_up_loop_runs_once_and_then_terminates(ctx):
    """The feedback edge is what makes this more than a pipeline - and what could hang it."""
    weak = AnalystOutput(claims=[make_claim()], gaps=["no lung data"], searched_queries=["q1"])
    better = AnalystOutput(claims=[make_claim(), make_claim("222")], searched_queries=["q1", "q2"])
    model = ScriptedModel(
        [weak, better],
        CriticOutput(verdict="issues_found", followup_queries=['"adagrasib" AND "NSCLC"']),
        EditorOutput(brief_markdown="A brief."),
    )
    result, gate = run_graph(model)
    path = [n.node_id for n in result.execution_order]

    assert path == ["analyst", "critic", "gate", "analyst", "critic", "gate", "editor"], (
        "the critic must re-examine the new claims before the gate decides again; a naive "
        "AND-join lets the gate re-fire on stale criticism"
    )
    assert gate.followup_used is True


def test_the_editor_structured_output_is_recoverable_from_the_graph_result(ctx):
    model = ScriptedModel(
        [AnalystOutput(claims=[make_claim()])],
        CriticOutput(verdict="no_issues_found_in_scope"),
        EditorOutput(brief_markdown="The evidence is colorectal.", headline="Weaker than it looks"),
    )
    result, _ = run_graph(model)
    editor = _editor_output(result)

    assert isinstance(editor, EditorOutput)
    assert editor.headline == "Weaker than it looks"


def test_tools_are_reachable_from_inside_a_graph_node(ctx, monkeypatch):
    """Proves budgets and the RunContext survive Strands' async tool dispatch."""

    from smartsynergy.sources.base import SearchResult

    class FakeEPMC:
        def search(self, query, max_records=8, **kw):  # noqa: ANN001
            return SearchResult(records=[make_source("333")], coverage="complete", total_hits=1)

    ctx.epmc = FakeEPMC()
    model = ScriptedModel(
        [AnalystOutput(claims=[make_claim()])],
        CriticOutput(verdict="no_issues_found_in_scope"),
        EditorOutput(brief_markdown="A brief."),
        tool_calls=[("search_literature", {"query": '"adagrasib" AND "cetuximab"', "max_results": 5})],
    )
    run_graph(model)

    assert ctx.tool_calls > 0, "the tool ran inside the graph and spent its budget"
    assert "333" in ctx.retrieved, "the tool wrote its results back into the run context"


def test_a_graph_run_cannot_execute_unbounded(ctx):
    model = ScriptedModel(
        [AnalystOutput(claims=[make_claim()])],
        CriticOutput(verdict="no_issues_found_in_scope"),
        EditorOutput(brief_markdown="A brief."),
    )
    result, _ = run_graph(model)
    assert len(result.execution_order) <= 9, "node execution cap protects an overnight budget"
