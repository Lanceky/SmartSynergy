from pathlib import Path

import pytest
from pydantic import ValidationError

from smartsynergy.config import DEFAULT_MODEL_ID, Settings, _ENV_MAP, get_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ENV_MAP.values():
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_without_env_or_dotenv():
    s = Settings.from_env(load_env_file=False)
    assert s.aws_region == "us-west-2"
    assert s.aws_profile is None
    assert s.model_id == DEFAULT_MODEL_ID
    assert s.source_mode == "live"
    assert s.db_path == Path("./data/smartsynergy.db")
    assert s.max_papers_per_run == 30
    assert s.demo_read_only is False
    assert "you@example.com" in s.user_agent


def test_env_overrides_and_type_coercion(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_PROFILE", "   ")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "us.amazon.nova-premier-v1:0")
    monkeypatch.setenv("SMARTSYNERGY_SOURCE_MODE", "replay")
    monkeypatch.setenv("SMARTSYNERGY_MAX_PAPERS_PER_RUN", "5")
    monkeypatch.setenv("SMARTSYNERGY_DB", "/tmp/x.db")
    monkeypatch.setenv("DEMO_READ_ONLY", "TRUE")
    s = Settings.from_env(load_env_file=False)
    assert s.aws_region == "eu-west-1"
    assert s.aws_profile is None  # blank profile means default credential chain
    assert s.model_id == "us.amazon.nova-premier-v1:0"
    assert s.source_mode == "replay"
    assert s.max_papers_per_run == 5
    assert s.db_path == Path("/tmp/x.db")
    assert s.demo_read_only is True


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("SMARTSYNERGY_SOURCE_MODE", "cached"),
        ("SMARTSYNERGY_MAX_PAPERS_PER_RUN", "0"),
        ("SMARTSYNERGY_RUN_TIMEOUT_S", "5"),
        ("DEMO_READ_ONLY", "maybe"),
    ],
)
def test_invalid_values_are_rejected(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        Settings.from_env(load_env_file=False)


def test_dotenv_file_does_not_override_existing_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("AWS_REGION=ap-southeast-1\nCONTACT_EMAIL=lab@example.org\n")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    s = Settings.from_env(dotenv_path=env_file)
    assert s.aws_region == "us-east-1"
    assert s.contact_email == "lab@example.org"


def test_get_settings_is_cached(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    first = get_settings()
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    assert get_settings() is first
    get_settings.cache_clear()
    assert get_settings().aws_region == "eu-central-1"
