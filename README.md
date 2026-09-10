# SmartSynergy

A contradiction-first evidence watch for oncology researchers, built with the [Strands Agents SDK](https://strandsagents.com) for the **Agents for Humans Hackathon** (Professional Agents track).

> **Status: early scaffold (Step 1 of 15).** The project brief is in [context.md](context.md) and the build plan in [implementation.md](implementation.md). Nothing described there should be read as implemented until this README says so.

**Research hypothesis for expert review; not medical advice.** This software never produces diagnoses, treatment recommendations, dosing, or safety clearances.

## What exists today

- Apache-2.0 license, pinned dependencies (`pyproject.toml`, `requirements.lock.txt`)
- `smartsynergy.config` — typed settings loaded from `.env`
- `scripts/smoke_bedrock.py` — one Amazon Bedrock call through a Strands `Agent`

## Quickstart (scaffold)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # set AWS_REGION / BEDROCK_MODEL_ID / credentials
pytest
python scripts/smoke_bedrock.py
```

The smoke test needs AWS credentials with access to the configured Bedrock model in the configured region.

## License

Code: [Apache-2.0](LICENSE). Third-party scientific data retrieved at runtime remains under its own terms (documented in `docs/data-licenses.md` once sources are integrated).
