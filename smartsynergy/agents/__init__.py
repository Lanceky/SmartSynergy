"""The three Strands agents. Judgment work only; control flow lives in gates.py/graph.py."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import boto3
from strands import Agent
from strands.models import BedrockModel
from strands.models.model import Model

from smartsynergy.config import Settings
from smartsynergy.schemas import AnalystOutput, CriticOutput, EditorOutput
from smartsynergy.tools import ANALYST_TOOLS, CRITIC_TOOLS

PROMPT_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text()


def _model(settings: Settings, temperature: float, override: Model | None = None) -> Model:
    if override is not None:  # offline tests inject a scripted model provider here
        return override
    # Explicit session so AWS_PROFILE from .env is honored and the region is unambiguous.
    session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
    return BedrockModel(model_id=settings.model_id, boto_session=session, temperature=temperature)


def make_analyst(settings: Settings, model: Model | None = None) -> Agent:
    return Agent(
        name="evidence_analyst",
        model=_model(settings, 0.1, model),
        system_prompt=load_prompt("analyst.md"),
        tools=ANALYST_TOOLS,
        structured_output_model=AnalystOutput,
        callback_handler=None,
    )


def make_critic(settings: Settings, model: Model | None = None) -> Agent:
    return Agent(
        name="contradiction_critic",
        model=_model(settings, 0.2, model),
        system_prompt=load_prompt("critic.md"),
        tools=CRITIC_TOOLS,
        structured_output_model=CriticOutput,
        callback_handler=None,
    )


def make_editor(settings: Settings, model: Model | None = None) -> Agent:
    # Deliberately no tools: the editor cannot introduce new sources or facts.
    return Agent(
        name="briefing_editor",
        model=_model(settings, 0.2, model),
        system_prompt=load_prompt("editor.md"),
        structured_output_model=EditorOutput,
        callback_handler=None,
    )
