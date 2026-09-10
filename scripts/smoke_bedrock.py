"""Step 1 smoke test: one Amazon Bedrock call through a Strands Agent.

Usage:
    python scripts/smoke_bedrock.py

Requires AWS credentials (env vars, AWS_PROFILE, or instance role) with access to the
model named by BEDROCK_MODEL_ID in AWS_REGION. Exits non-zero with a readable hint on failure.
"""

from __future__ import annotations

import sys
import time

from smartsynergy.config import get_settings


def main() -> int:
    settings = get_settings()
    print(f"region={settings.aws_region} profile={settings.aws_profile or '<default chain>'} model={settings.model_id}")

    try:
        from strands import Agent
        from strands.models import BedrockModel
    except ImportError as exc:  # pragma: no cover - environment problem, not logic
        print(f"strands-agents is not installed: {exc}", file=sys.stderr)
        return 2

    agent = Agent(
        model=BedrockModel(model_id=settings.model_id, region_name=settings.aws_region, temperature=0.0),
        system_prompt="You are a connectivity check. Follow the instruction literally.",
        callback_handler=None,  # no streaming to stdout; we print the final text ourselves
    )

    started = time.perf_counter()
    try:
        result = agent("Reply with the single word OK.")
    except Exception as exc:  # surface the AWS error text; it is usually self-explanatory
        print(f"Bedrock call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Hints: check credentials (aws sts get-caller-identity), that the model is enabled in this region, "
            "and that BEDROCK_MODEL_ID is spelled exactly as listed by `aws bedrock list-foundation-models`.",
            file=sys.stderr,
        )
        return 1
    elapsed = time.perf_counter() - started

    text = str(result).strip()
    usage = result.metrics.accumulated_usage
    print(f"reply={text!r}")
    print(
        f"latency_s={elapsed:.2f} input_tokens={usage.get('inputTokens')} "
        f"output_tokens={usage.get('outputTokens')} stop_reason={result.stop_reason}"
    )
    return 0 if "ok" in text.lower() else 1


if __name__ == "__main__":
    sys.exit(main())
