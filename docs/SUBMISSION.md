# Devpost submission text

Copy-paste ready. Track: **Professional Agents**.

---

## Elevator pitch (200 char limit)

> It reads the cancer literature every night, argues with itself about what it found, and wakes you only when the evidence actually changes.

---

## Inspiration

A translational oncology researcher tracks a dozen combination-therapy hypotheses at once — *does pairing this KRAS G12C inhibitor with that second agent actually hold up?*

Answering it once is easy. Answering it **again, every week, forever** is what breaks people. Roughly 4,000 new papers are indexed in Europe PMC every day, and the relevant ones arrive unannounced. Miss a retraction and you cite a finding that no longer exists. Miss a negative trial and you spend six months re-learning it.

The failure isn't a lack of intelligence. It's that staying current is unbounded, repetitive work with no natural stopping point — exactly the kind of work a background agent should own.

## What it does

SmartSynergy is a standing evidence watch. It runs on a schedule, monitors a small panel of combination-therapy hypotheses, and interrupts a human for exactly four reasons:

- new directly-applicable evidence
- new evidence **against** the hypothesis
- an applicability problem (evidence you were relying on turns out to be from another tumour type)
- a source was corrected or retracted

Anything else — a new paper repeating a known result, a rephrasing, a review citing work already in the ledger — is recorded and never surfaced. On a normal day the correct output is **nothing**.

When it does speak, it hands over a decision card: what changed, why it matters, and every claim with the verbatim passage it came from.

## Who it's for

Translational oncology researchers, and the small biotechs and academic labs that decide which experiments to run next without an informatics team behind them.

## How we built it

Four nodes in a **Strands Graph**, not a chat loop:

1. **Analyst** (Strands Agent, `structured_output_model=AnalystOutput`) searches Europe PMC and PubChem and proposes claims, each quoting its source verbatim.
2. **Critic** (a *separate* Strands Agent with its own tools) exists only to attack those claims — wrong tumour type, wrong variant, missing comparator, a retraction.
3. **Evidence gate** — a custom `MultiAgentBase` graph node that is **not a model**. It's plain Python, and it's where trust is enforced.
4. **Editor** writes the brief; a final gate rejects it outright if it cites a claim ID that doesn't exist.

A conditional edge lets the gate send the Analyst back for **exactly one** follow-up pass.

**The gate is the whole point.** It:
- deletes any claim whose `source_id` was never returned by a tool — a fabricated citation physically cannot reach a human
- verifies every quote is a substring of the stored source text, else flags it unverified in red
- **forcibly demotes** direct evidence to indirect when the Critic finds a context mismatch — the model doesn't get a vote

To stay quiet, every assessment reduces to a fingerprint over its claims, blind to wording and ordering. Idempotency is enforced by a database constraint, not by application logic: the same change cannot alert you twice.

There is deliberately **no synergy score**. A single number would demo beautifully and be indefensible — you can't average a cell-line result and a phase III trial. The dimensions stay separate, and the system reports a *state*, not a score. The distinction that matters most is between "no evidence" and "evidence of harm", and a score erases it.

## Challenges we ran into

Two came from actually running the graph rather than reasoning about it.

**Stale criticism.** A Strands `Graph` node fires when *any* incoming edge completes. After the gate sent the Analyst back for a second pass, the Critic's result from the previous pass was still `COMPLETED` — so the gate re-fired against criticism of claims that no longer existed. The AND-join into the gate is now ordering-aware, and a test pins the exact path: `analyst → critic → gate → analyst → critic → gate → editor`.

**Structured output is a forced tool call.** Strands implements `structured_output_model` by injecting a tool named after the Pydantic model and forcing the model to invoke it — it does *not* call `Model.structured_output()`. That only became visible when we wrote a custom `Model` provider.

## Accomplishments we're proud of

The entire Strands graph is tested **offline**. A scripted `Model` provider lets the real graph — real agents, real tools, real gate node, real conditional edges — execute in CI with no network and no AWS account. 45 tests, all passing without credentials.

And the demo uses real literature: `scripts/seed_demo.py` fetches live Europe PMC records and plays three consecutive days through the real pipeline. Day 1 builds a baseline and interrupts nobody. Day 2 is silent. Day 3 finds a contradiction and raises exactly one item. Every quoted passage is verified verbatim against the live abstract.

## What we learned

Prompts are requests; gates are rules. Every property we actually depend on — no fabricated citations, no cross-context evidence, no duplicate alerts, no runaway loops — lives in deterministic Python with a test that fails if the rule is removed. The model does judgment. It does not do trust.

## What's next

AgentCore deployment with an EventBridge schedule (the entry point already returns a JSON-serialisable summary), a DynamoDB storage backend for multi-user watches, and clinical-trial registry ingestion so an unpublished negative trial can trigger an alert before it reaches the literature.

## Built with

Python · Strands Agents SDK · Amazon Bedrock (Nova Pro) · Pydantic · SQLite · Streamlit · Europe PMC REST API · PubChem PUG REST

## Try it out

- Repo: https://github.com/Lanceky/SmartSynergy
- `python scripts/seed_demo.py` runs the full story with real papers and no AWS account.

---

## Submission checklist

| Requirement | Where it is |
| --- | --- |
| Text description | this file (paste into Devpost) |
| Public code repo | https://github.com/Lanceky/SmartSynergy — **set to public** |
| Setup instructions | `README.md` → Quickstart |
| Apache-2.0 license, visible in About | `LICENSE` — also pick it in the repo's About panel |
| README | `README.md` |
| **Architecture diagram** | `docs/architecture.png` — upload this to Devpost |
| Demo video (≤5 min) | record from `docs/DEMO.md` |
| AWS Builder ID | add yours on the Devpost form |
| Live demo (optional) | Streamlit console, run locally |

The diagram is generated, not drawn: `python scripts/make_architecture_diagram.py`
rewrites `docs/architecture.svg`, so it cannot drift from the system it describes.
