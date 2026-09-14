"""Preflight: check everything SmartSynergy needs, and say precisely what is wrong.

    python -m smartsynergy.worker doctor

Checks run in dependency order and stop being useful once one fails, so each one prints a
specific remedy rather than a stack trace.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from smartsynergy.config import Settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""

    def render(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        out = f"[{mark}] {self.name}: {self.detail}"
        if not self.ok and self.fix:
            out += f"\n        fix: {self.fix}"
        return out


def check_dependencies() -> Check:
    try:
        import strands  # noqa: F401

        from strands.multiagent import GraphBuilder  # noqa: F401

        from importlib.metadata import version

        return Check("dependencies", True, f"strands-agents {version('strands-agents')} importable")
    except Exception as exc:  # noqa: BLE001
        return Check("dependencies", False, str(exc), "pip install -r requirements.lock.txt")


def check_database(settings: Settings) -> Check:
    try:
        from smartsynergy.storage import Store

        with Store(settings.db_path) as store:
            watches = store.list_watches()
        if not watches:
            return Check(
                "database", False, f"{settings.db_path} has no watches",
                "python -m smartsynergy.worker init-watch fixtures/panel.json",
            )
        return Check("database", True, f"{len(watches)} watch(es) at {settings.db_path}")
    except Exception as exc:  # noqa: BLE001
        return Check("database", False, f"{type(exc).__name__}: {exc}", "check write permissions on ./data")


def check_sources(settings: Settings) -> Check:
    try:
        from smartsynergy.sources import EuropePMC

        epmc = EuropePMC(settings.user_agent)
        result = epmc.search('KRAS AND "G12C" AND sotorasib', max_records=1, use_cache=False)
        epmc.close()
        if not result.records:
            return Check("europe pmc", False, "reachable but returned no records", "retry; the API may be busy")
        return Check("europe pmc", True, f"{result.total_hits} hits for a probe query")
    except Exception as exc:  # noqa: BLE001
        return Check("europe pmc", False, f"{type(exc).__name__}: {exc}", "check outbound network access")


def check_credentials(settings: Settings) -> Check:
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError as exc:  # pragma: no cover
        return Check("aws credentials", False, str(exc), "pip install boto3")

    try:
        session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        identity = session.client("sts").get_caller_identity()
        return Check("aws credentials", True, f"{identity['Arn']} in {settings.aws_region}")
    except NoCredentialsError:
        return Check(
            "aws credentials", False, "no credentials found",
            "set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in .env, or run `aws configure`",
        )
    except (ClientError, BotoCoreError) as exc:
        return Check("aws credentials", False, f"{type(exc).__name__}: {exc}", "verify the key is active and not expired")


def check_model(settings: Settings) -> Check:
    """The single most common setup failure: credentials work, model access was never granted."""
    try:
        import boto3
        from strands import Agent
        from strands.models import BedrockModel

        session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        agent = Agent(
            model=BedrockModel(model_id=settings.model_id, boto_session=session, temperature=0.0),
            system_prompt="Reply with exactly the word OK.",
            callback_handler=None,
        )
        started = time.perf_counter()
        result = agent("Reply with the single word OK.")
        elapsed = time.perf_counter() - started
        return Check("bedrock model", True, f"{settings.model_id} replied in {elapsed:.1f}s")
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        fix = "open the Bedrock console -> Model access and enable this model in " + settings.aws_region
        if "AccessDenied" in message or "not authorized" in message:
            fix = "attach AmazonBedrockFullAccess to this IAM identity, then enable the model in the Bedrock console"
        elif "ValidationException" in message or "not found" in message.lower():
            fix = (
                f"BEDROCK_MODEL_ID={settings.model_id} may be wrong for {settings.aws_region}; "
                "try us.amazon.nova-pro-v1:0, which needs no access request"
            )
        return Check("bedrock model", False, f"{type(exc).__name__}: {message[:200]}", fix)


def check_structured_output(settings: Settings) -> Check:
    """Strands implements structured output as a forced tool call; not every model obliges."""
    try:
        import boto3
        from strands import Agent
        from strands.models import BedrockModel

        from smartsynergy.schemas import CriticOutput

        session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        agent = Agent(
            model=BedrockModel(model_id=settings.model_id, boto_session=session, temperature=0.0),
            system_prompt="You assess evidence. Always answer with the structured output tool.",
            structured_output_model=CriticOutput,
            callback_handler=None,
        )
        result = agent("There is no evidence to challenge. Report no issues found.")
        out = getattr(result, "structured_output", None)
        if isinstance(out, CriticOutput):
            return Check("structured output", True, f"model returned a valid CriticOutput ({out.verdict})")
        return Check(
            "structured output", False, f"expected CriticOutput, got {type(out).__name__}",
            "choose a model with strong tool-use support, e.g. us.amazon.nova-pro-v1:0",
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            "structured output", False, f"{type(exc).__name__}: {str(exc)[:200]}",
            "the model must support tool use; try us.amazon.nova-pro-v1:0",
        )


def run(settings: Settings, skip_aws: bool = False) -> int:
    checks = [check_dependencies(), check_database(settings), check_sources(settings)]
    if not skip_aws:
        creds = check_credentials(settings)
        checks.append(creds)
        if creds.ok:
            model = check_model(settings)
            checks.append(model)
            if model.ok:
                checks.append(check_structured_output(settings))

    print(f"region={settings.aws_region}  model={settings.model_id}  db={settings.db_path}\n")
    for c in checks:
        print(c.render())

    failed = [c for c in checks if not c.ok]
    print()
    if failed:
        print(f"{len(failed)} check(s) failed. Everything except the AWS checks runs offline;")
        print("the watch logic, gates and UI are fully testable with `pytest tests/`.")
        return 1
    print("All checks passed. Run: python -m smartsynergy.worker run-watch <watch_id>")
    return 0
