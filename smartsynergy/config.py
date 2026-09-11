"""Typed runtime settings loaded from environment variables and an optional .env file.

Budgets defined here are enforced in code (tools, controller, worker), never only in prompts.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

SourceMode = Literal["live", "replay"]

# Strands' documented default Bedrock model; override with BEDROCK_MODEL_ID.
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"

_ENV_MAP: dict[str, str] = {
    "aws_region": "AWS_REGION",
    "aws_profile": "AWS_PROFILE",
    "model_id": "BEDROCK_MODEL_ID",
    "db_path": "SMARTSYNERGY_DB",
    "source_mode": "SMARTSYNERGY_SOURCE_MODE",
    "max_papers_per_run": "SMARTSYNERGY_MAX_PAPERS_PER_RUN",
    "max_model_calls_per_run": "SMARTSYNERGY_MAX_MODEL_CALLS_PER_RUN",
    "max_tool_calls_per_run": "SMARTSYNERGY_MAX_TOOL_CALLS_PER_RUN",
    "run_timeout_s": "SMARTSYNERGY_RUN_TIMEOUT_S",
    "node_timeout_s": "SMARTSYNERGY_NODE_TIMEOUT_S",
    "max_runs_per_day": "SMARTSYNERGY_MAX_RUNS_PER_DAY",
    "contact_email": "CONTACT_EMAIL",
    "demo_read_only": "DEMO_READ_ONLY",
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class Settings(BaseModel):
    """All knobs the application reads. Defaults are safe for a local replay run."""

    aws_region: str = "us-west-2"
    aws_profile: str | None = None
    model_id: str = DEFAULT_MODEL_ID

    db_path: Path = Path("./data/smartsynergy.db")
    source_mode: SourceMode = "live"

    max_papers_per_run: int = Field(default=30, ge=1)
    max_model_calls_per_run: int = Field(default=12, ge=1)
    max_tool_calls_per_run: int = Field(default=40, ge=1)
    run_timeout_s: int = Field(default=300, ge=30)
    node_timeout_s: int = Field(default=120, ge=10)
    max_runs_per_day: int = Field(default=6, ge=1)

    contact_email: str = "you@example.com"
    demo_read_only: bool = False

    @field_validator("aws_profile", mode="before")
    @classmethod
    def _empty_profile_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("demo_read_only", mode="before")
    @classmethod
    def _parse_bool(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in _TRUE:
                return True
            if lowered in _FALSE:
                return False
            raise ValueError(f"expected a boolean-like string, got {value!r}")
        return value

    @property
    def user_agent(self) -> str:
        """Identifies this tool to public APIs (Europe PMC, PubChem) as good-citizen practice."""
        return f"SmartSynergy/0.1 (contact: {self.contact_email})"

    @classmethod
    def from_env(cls, dotenv_path: str | os.PathLike[str] | None = None, *, load_env_file: bool = True) -> "Settings":
        """Build settings from process environment, optionally loading a .env file first.

        Existing environment variables always win over .env values.
        """
        if load_env_file:
            load_dotenv(dotenv_path=dotenv_path, override=False)
        # boto3 treats AWS_PROFILE="" as a profile literally named "" and raises ProfileNotFound,
        # so a blank line in .env must mean "use the default credential chain".
        if os.environ.get("AWS_PROFILE", None) is not None and not os.environ["AWS_PROFILE"].strip():
            os.environ.pop("AWS_PROFILE")
        raw = {field: os.environ[var] for field, var in _ENV_MAP.items() if var in os.environ}
        return cls(**raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings; call `get_settings.cache_clear()` in tests after mutating the environment."""
    return Settings.from_env()
