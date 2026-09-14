"""A scripted Strands model provider for offline testing.

The Bedrock-backed path cannot be exercised without credentials, but the *wiring* around it
can and should be: graph topology, the AND-join into the evidence gate, conditional routing,
the bounded follow-up loop, tool dispatch and structured-output plumbing.

This implements the Strands `Model` interface with canned responses, so the entire graph runs
end to end with no network and no AWS account.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Iterable

from strands.models.model import Model

from smartsynergy.schemas import AnalystOutput, CriticOutput, EditorOutput


class ScriptedModel(Model):
    """Returns pre-written structured outputs, optionally calling tools first.

    Args:
        analyst_outputs: One AnalystOutput per Analyst turn. The last is reused if the graph
            revisits the node more often than expected.
        critic_output: What the Critic returns.
        editor_output: What the Editor returns.
        tool_calls: Optional (tool_name, input) pairs the model should request before answering,
            used to prove the real @tool functions are reachable from inside the graph.
    """

    def __init__(
        self,
        analyst_outputs: Iterable[AnalystOutput],
        critic_output: CriticOutput,
        editor_output: EditorOutput,
        tool_calls: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> None:
        self._analyst = list(analyst_outputs)
        self._critic = critic_output
        self._editor = editor_output
        self._tool_calls = list(tool_calls or [])
        self.analyst_turns = 0
        self.structured_calls: list[str] = []
        self.stream_calls = 0

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> dict[str, Any]:
        return {"model_id": "scripted-offline"}

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):  # noqa: ANN001
        """Optionally call a real tool, then satisfy the injected structured-output tool.

        Strands implements `structured_output_model` by injecting a tool named after the
        Pydantic model and forcing the model to call it - it does NOT call
        `Model.structured_output()`. A scripted provider has to do the same thing.
        """
        import json

        self.stream_calls += 1
        available = {s["name"] for s in (tool_specs or [])}

        pending = next(((n, i) for n, i in self._tool_calls if n in available), None)
        if pending and not self._already_called(messages, pending[0]):
            name, tool_input = pending
            async for event in self._emit_tool_use(name, json.dumps(tool_input)):
                yield event
            return

        for model_cls in (AnalystOutput, CriticOutput, EditorOutput):
            if model_cls.__name__ in available:
                payload = self._payload_for(model_cls)
                async for event in self._emit_tool_use(model_cls.__name__, payload.model_dump_json()):
                    yield event
                return

        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": "Analysis complete."}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}

    def _payload_for(self, model_cls):  # noqa: ANN001
        self.structured_calls.append(model_cls.__name__)
        if model_cls is AnalystOutput:
            idx = min(self.analyst_turns, len(self._analyst) - 1)
            self.analyst_turns += 1
            return self._analyst[idx]
        if model_cls is CriticOutput:
            return self._critic
        return self._editor

    async def _emit_tool_use(self, name: str, payload: str):
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"tu-{self.stream_calls}", "name": name}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": payload}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}

    @staticmethod
    def _already_called(messages, tool_name: str) -> bool:
        for m in messages:
            for block in m.get("content", []) or []:
                if isinstance(block, dict) and block.get("toolResult"):
                    return True
                if isinstance(block, dict) and block.get("toolUse", {}).get("name") == tool_name:
                    return True
        return False

    async def structured_output(  # type: ignore[override]
        self, output_model, prompt, system_prompt=None, **kwargs  # noqa: ANN001
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.structured_calls.append(output_model.__name__)
        if output_model is AnalystOutput:
            idx = min(self.analyst_turns, len(self._analyst) - 1)
            self.analyst_turns += 1
            yield {"output": self._analyst[idx]}
        elif output_model is CriticOutput:
            yield {"output": self._critic}
        elif output_model is EditorOutput:
            yield {"output": self._editor}
        else:  # pragma: no cover - defensive
            yield {"output": output_model()}
