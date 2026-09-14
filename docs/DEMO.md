# Demo script (target: 4 minutes 30 seconds)

Everything below runs today, with no AWS credentials, using real Europe PMC records.

## Setup before recording

```bash
rm -f data/smartsynergy.db
.venv/bin/python scripts/seed_demo.py          # fetches live literature, plays 3 days
.venv/bin/python -m streamlit run app/streamlit_app.py
```

---

## 0:00 – 0:40 · The problem

> "A translational oncologist is tracking a dozen combination-therapy hypotheses. Answering *does this pairing hold up?* isn't hard. Answering it **again, every week, forever** is what breaks people.
>
> Four thousand papers are indexed in Europe PMC every day. Miss a retraction and you cite a finding that no longer exists. Miss a negative trial and you spend six months re-learning it.
>
> So they check sporadically, and quietly rely on assumptions that stopped being true months ago."

**Show:** the Europe PMC search page with a hit count in the thousands.

## 0:40 – 1:10 · Why an LLM alone makes this worse

> "Ask a chatbot and it will answer confidently — and it will cite papers that don't exist, and quote a **colorectal** result as if it applied to **lung**.
>
> SmartSynergy is built on the assumption that its own model output cannot be trusted."

**Show:** the README's trust table.

## 1:10 – 1:50 · The agent runs on a schedule, not on demand

**Terminal:**

```bash
cat deploy/cron.example
.venv/bin/python -m smartsynergy.worker run-watch kras-g12c-nsclc
```

> "This is a cron job, not an app. On most days the correct output is nothing at all."

**Show:** the run completing, then:

```bash
.venv/bin/python -m smartsynergy.worker inbox
```

## 1:50 – 2:50 · The architecture — the part that earns trust

**Show:** the Mermaid diagram in the README.

> "Four nodes in a Strands Graph. An **Analyst** proposes claims, each quoting its source verbatim. A **Critic** — a separate agent, with its own tools — attacks them.
>
> Then the **evidence gate**. It's a graph node, but it is not a model. It's plain Python, and it's where trust is enforced:
>
> - a claim about a source the agent never actually retrieved is **deleted** — a fabricated citation cannot reach you
> - a quote that isn't a substring of the stored text is flagged as unverified
> - a critic finding of *wrong tumour type* **forcibly demotes** direct evidence to indirect. The model doesn't get a vote.
>
> The gate can send the Analyst back — exactly once — then it must decide."

**Show:** `smartsynergy/gates.py`, then `pytest tests/ -q` → 51 passing.

> "Including the whole Strands graph, run offline against a scripted model provider."

## 2:50 – 3:50 · The product

**Show:** the Streamlit console.

**Decision inbox:**

> "One item. Not a digest, not a feed — a decision.
>
> *Support for this pair is weaker than it looks: the strongest evidence is colorectal, not lung.*"

**Expand "Check the agent's work":**

> "Every claim carries its verbatim passage, the licence, the study design, and whether the quote was found in the source. These are real papers, fetched live."

**Hypotheses page:**

> "Notice there is **no synergy score**. A single number would demo beautifully and mean nothing — you can't average a cell-line result and a phase III trial.
>
> So the dimensions stay apart. Direct support: zero. Indirect: one — demoted by the critic. Opposing: one. The distinction that matters is between *no evidence* and *evidence of harm*, and a score erases it."

**Run history:**

> "Three runs. Day one built a baseline and interrupted nobody. Day two found nothing and said nothing. Day three found a contradiction and spoke up."

## 3:50 – 4:20 · Why it stays quiet

> "Every assessment reduces to a fingerprint over its claims — blind to wording and ordering. Same evidence, same fingerprint, no alert. Ever.
>
> And it's enforced in the database: a unique constraint on the fingerprint means the same change **physically cannot** alert you twice.
>
> A watch that pings you daily is a watch you stop reading."

## 4:20 – 4:30 · Who it's for, and why it matters

> "It's for the people deciding which experiments are worth running next — in academic labs and small biotechs without an informatics team.
>
> It won't tell you a drug combination works. It tells you what the literature says, where the evidence came from, and — most usefully — **where it does not apply**.
>
> Apache-2.0. Everything you just saw is in the repo."

---

## Checklist

- [ ] Terminal font ≥ 16pt
- [ ] `run-watch` output visible end to end
- [ ] `pytest` pass count on screen
- [ ] Architecture diagram on screen for ≥ 10s
- [ ] At least one evidence receipt expanded, showing a real quote and a real Europe PMC link
- [ ] Under 5:00
