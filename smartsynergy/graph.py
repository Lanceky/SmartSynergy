"""Strands Graph assembly: Analyst -> Critic -> [deterministic gate] -> Editor, with one
bounded feedback edge back to the Analyst.

The gate is a custom node (MultiAgentBase), so trust decisions run as ordinary Python
inside the graph rather than being requested politely from a model.
"""

from __future__ import annotations

import json
import time
from typing import Any

from strands.agent.agent_result import AgentResult
from strands.multiagent import GraphBuilder
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status
from strands.multiagent.graph import GraphState
from strands.types.content import ContentBlock, Message

from smartsynergy.agents import make_analyst, make_critic, make_editor
from smartsynergy.config import Settings
from smartsynergy.gates import GateResult, evidence_gate
from smartsynergy.schemas import AnalystOutput, CriticOutput
from smartsynergy.tools import get_context


def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    """Pull every top-level JSON object out of mixed text (the graph concatenates node results)."""
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []
    i = 0
    while i < len(text):
        start = text.find("{", i)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                found.append(obj)
            i = end
        except ValueError:
            i = start + 1
    return found


def parse_upstream(text: str) -> tuple[AnalystOutput, CriticOutput, bool]:
    """Recover the Analyst and Critic structured outputs from the node input text.

    Returns (analyst, critic, critic_reported). The third value matters: the default
    CriticOutput is `no_issues_found_in_scope`, which is indistinguishable from a critic
    that genuinely found nothing wrong. Silently treating a missing or malformed critic
    reply as an endorsement would fail open on the one step the whole design rests on,
    so the caller is told whether a critic actually reported.
    """
    analyst, critic = AnalystOutput(), CriticOutput(verdict="no_issues_found_in_scope")
    critic_reported = False
    for obj in _extract_json_objects(text):
        if "claims" in obj and "searched_queries" in obj:
            try:
                analyst = AnalystOutput.model_validate(obj)
            except Exception:  # noqa: BLE001 - malformed output is a gate failure, not a crash
                pass
        elif "verdict" in obj:
            try:
                critic = CriticOutput.model_validate(obj)
                critic_reported = True
            except Exception:  # noqa: BLE001
                pass
    return analyst, critic, critic_reported


def _as_node_result(name: str, text: str, started: float) -> MultiAgentResult:
    agent_result = AgentResult(
        stop_reason="end_turn",
        message=Message(role="assistant", content=[ContentBlock(text=text)]),
        metrics=None,
        state={},
    )
    elapsed = int((time.perf_counter() - started) * 1000)
    return MultiAgentResult(
        status=Status.COMPLETED,
        results={name: NodeResult(result=agent_result, execution_time=elapsed, status=Status.COMPLETED)},
        execution_time=elapsed,
    )


class EvidenceGateNode(MultiAgentBase):
    """Deterministic node: validates claims, applies challenges, decides on one follow-up."""

    def __init__(self, hypothesis_id: str, name: str = "gate") -> None:
        super().__init__()
        self.hypothesis_id = hypothesis_id
        self.name = name
        self.last_result: GateResult | None = None
        self.followup_used = False

    async def invoke_async(self, task, invocation_state=None, **kwargs) -> MultiAgentResult:  # noqa: ANN001
        started = time.perf_counter()
        text = task if isinstance(task, str) else "\n".join(
            getattr(b, "text", "") or (b.get("text", "") if isinstance(b, dict) else "") for b in task
        )
        analyst, critic, critic_reported = parse_upstream(text)
        ctx = get_context()

        result = evidence_gate(
            analyst,
            critic,
            hypothesis_id=self.hypothesis_id,
            run_id=ctx.run_id,
            known_sources=ctx.retrieved,
            followup_used=self.followup_used,
            critic_reported=critic_reported,
        )
        if result.needs_followup:
            self.followup_used = True
        self.last_result = result

        payload = {
            "accepted_claims": [
                {
                    "claim_id": c.claim_id,
                    "source_id": c.source_id,
                    "finding": c.finding,
                    "label": c.label,
                    "context_match": c.context_match,
                    "study_design": c.study_design,
                    "limitations": c.limitations,
                    "passage_verified": c.passage_verified,
                    "challenges": c.challenged_by,
                }
                for c in result.accepted
            ],
            "rejected": [{"source_id": s, "why": w} for s, w in result.rejected],
            "gaps": result.gaps,
            "followup_queries": result.followup_queries if result.needs_followup else [],
        }
        body = f"{result.marker}\n{json.dumps(payload, indent=1)}"
        if result.needs_followup:
            body += (
                "\n\nRun these follow-up searches and return an updated, complete AnalystOutput:\n"
                + "\n".join(f"- {q}" for q in result.followup_queries)
            )
        return _as_node_result(self.name, body, started)

    def __call__(self, task, invocation_state=None, **kwargs):  # pragma: no cover - sync path
        import asyncio

        return asyncio.run(self.invoke_async(task, invocation_state, **kwargs))


def _gate_says(gate: "EvidenceGateNode", marker: str):
    """Route on the gate's own decision object rather than parsing its rendered text."""

    def condition(state: GraphState) -> bool:
        if "gate" not in state.results or gate.last_result is None:
            return False
        return gate.last_result.marker == marker

    return condition


def _both_done(*node_ids: str):
    """Python graphs fire on ANY incoming edge; the gate must wait for BOTH upstream agents.

    Completion alone is not enough. After the gate sends the Analyst back for a follow-up pass,
    the Critic's result from the *previous* pass is still marked COMPLETED, so a naive check
    lets the gate re-fire against stale criticism before the Critic has seen the new claims.
    The order of the current execution is therefore what decides: every upstream node must have
    run more recently than the first one listed.
    """

    def condition(state: GraphState) -> bool:
        if not all(
            n in state.results and state.results[n].status == Status.COMPLETED for n in node_ids
        ):
            return False
        order = [n.node_id for n in state.execution_order]
        try:
            last_seen = [len(order) - 1 - order[::-1].index(n) for n in node_ids]
        except ValueError:
            return False
        # The first node listed (the Analyst) starts a pass; every other must follow it.
        return all(idx > last_seen[0] for idx in last_seen[1:])

    return condition


def build_graph(
    settings: Settings, hypothesis_id: str, model: Any | None = None
) -> tuple[Any, EvidenceGateNode]:
    """Assemble the graph. `model` injects a scripted provider for offline tests."""
    gate = EvidenceGateNode(hypothesis_id)
    b = GraphBuilder()
    b.add_node(make_analyst(settings, model), "analyst")
    b.add_node(make_critic(settings, model), "critic")
    b.add_node(gate, "gate")
    b.add_node(make_editor(settings, model), "editor")

    b.add_edge("analyst", "critic")
    b.add_edge("analyst", "gate", condition=_both_done("analyst", "critic"))
    b.add_edge("critic", "gate", condition=_both_done("analyst", "critic"))
    b.add_edge("gate", "analyst", condition=_gate_says(gate, "FOLLOWUP_REQUIRED"))
    b.add_edge("gate", "editor", condition=_gate_says(gate, "GATE_PASSED"))

    b.set_entry_point("analyst")
    b.set_max_node_executions(9)  # backstop; the gate itself enforces the one-follow-up rule
    b.set_execution_timeout(float(settings.run_timeout_s))
    b.set_node_timeout(float(settings.node_timeout_s))
    b.reset_on_revisit(True)
    return b.build(), gate
