# Project Context: Combination Therapy Hypothesis Engine

## Hackathon
**Agents for Humans Hackathon** (AWS / Devpost)
**Track:** Professional Agents
**SDK:** Strands Agents SDK
**Deployment target:** Amazon Bedrock AgentCore (stretch goal, strengthens Technical Implementation score)

---

## 1. Problem Statement

Oncology researchers investigating combination therapies for specific cancer mutations must manually:
- Trace a mutation to its affected molecular pathways
- Cross-reference dozens of drug and compound databases for anything touching those pathways
- Read through hundreds of papers to check whether any combination has been studied, and how strong that evidence actually is
- Manually check for known drug-drug interaction risks
- Synthesize all of this into a shortlist worth pursuing experimentally

This process takes days to weeks per mutation and does not scale. Most of the work is not the final judgment call (which a human expert must still make) — it's the exhausting, repetitive information-gathering and cross-referencing that precedes it.

## 2. Who It's For

Bench researchers, oncology grad students, and small computational biology labs who need to generate and triage combination-therapy hypotheses without access to a large team or a dedicated bioinformatics pipeline.

## 3. Why It Matters

Combination therapies are one of the more promising near-term paths to better cancer outcomes, because they often use *already-approved* drugs — meaning faster paths to trials than novel drug discovery. The bottleneck is not creativity, it's the sheer volume of literature and data a researcher must sift through to find a promising, safe, mechanistically justified combination. Automating that triage step lets researchers spend their time on lab work and judgment calls, not manual search.

## 4. What This Agent Is — and Is Not

**It is:** a multi-agent research assistant that generates, evidence-checks, and ranks candidate drug/compound combinations against a specified mutation or pathway, with full citations and confidence scoring, for expert review.

**It is not:** a diagnostic tool, a treatment recommendation engine, or a clinical decision-support system. It never outputs anything framed as medical advice. Every output is explicitly a "hypothesis for researcher review," not a conclusion.

This distinction is core to the project's credibility — scientifically and ethically.

## 5. Scope for the Hackathon Demo

To keep the build viable in the timeframe, the demo is scoped to **one well-studied case**:
- **Mutation:** KRAS G12C
- **Cancer type:** Pancreatic and/or non-small-cell lung cancer
- **Rationale:** well-characterized pathway, existing approved inhibitors (e.g., sotorasib), rich literature — good signal-to-noise for a live demo, without needing to claim general-purpose applicability.

The architecture is designed to generalize to other mutations, but the demo proves depth on one case rather than shallow breadth across many.

## 6. System Architecture — Multi-Agent Design

An orchestrator coordinates five specialized sub-agents, each with its own tools, rather than a single agent chaining tool calls linearly.

```
                     ┌──────────────────────┐
                     │   Orchestrator Agent  │
                     └──────────┬───────────┘
        ┌───────────┬───────────┼───────────┬────────────┐
        ▼           ▼           ▼            ▼            ▼
   Pathway      Compound     Literature    Safety      Synthesis
   Analyst      Scout        Critic        Auditor     Agent
```

### 6.1 Pathway Analyst Agent
- **Input:** mutation / gene symbol (e.g., KRAS G12C)
- **Tools:** pathway database lookup (KEGG / Reactome API)
- **Output:** list of affected pathways and downstream targets

### 6.2 Compound Scout Agent
- **Input:** targets/pathways from Pathway Analyst
- **Tools:** DrugBank / PubChem API queries (approved drugs and well-studied bioactive compounds)
- **Output:** candidate compounds/drugs that act on those targets, individually

### 6.3 Literature Critic Agent
- **Input:** candidate compound pairs
- **Tools:** PubMed E-utilities API search
- **Output:** for each candidate pair — supporting studies found, and a critique of evidence quality (in vitro vs in vivo vs clinical, sample size, contradicting findings)
- **Key behavior:** this agent is a *critic*, not just a retriever — it actively looks for contradicting evidence, not just confirming evidence.

### 6.4 Safety Auditor Agent
- **Input:** candidate compound pairs
- **Tools:** drug-drug / herb-drug interaction lookup
- **Output:** flags on any known dangerous interactions; filters or down-ranks unsafe combinations

### 6.5 Synthesis Agent
- **Input:** outputs from all four agents above
- **Process:** computes a transparent composite "synergy confidence score" per candidate:
  - Mechanistic overlap (do the pathways actually converge?)
  - Evidence strength (quality/quantity of supporting literature)
  - Safety margin (interaction risk)
- **Output:** ranked list of candidate combinations, each with reasoning chain and citations

### 6.6 Refinement Loop
The Synthesis Agent's top candidates are sent back to the Literature Critic Agent for a second pass: *"find any evidence that contradicts this specific combination."* This closes the loop and demonstrates genuine iterative reasoning rather than a single linear pass — this is the clearest "agentic" behavior to highlight in the demo.

## 7. Data Sources (all real, all citable)
- **KEGG** or **Reactome** — pathway data
- **PubChem** / **DrugBank** (open dataset) — compound and drug data
- **PubMed E-utilities API** — literature search
- Known drug interaction dataset (open source) — safety checks

No fabricated or synthetic data. Every claim in the final output must trace back to a retrieved source.

## 8. Optional Stretch Goals
- **Knowledge graph construction**: agents write intermediate findings (gene → pathway → drug → evidence) into a lightweight in-memory graph. Visualized in the demo and reused as the architecture diagram deliverable.
- **Amazon Bedrock AgentCore deployment**: run as a persistent, observable agent runtime rather than a local script.

## 9. Track Fit — Professional Agents

> "Every profession has work that only a person can judge and a pile of repetitive tasks around it that shouldn't need one... the solo researcher facing a mountain of papers."

This project is a direct, literal fit: the target user is exactly "the solo researcher facing a mountain of papers." The agent clears the repetitive cross-referencing runway so the researcher can spend their time on the judgment call the science actually requires.

**Not a fit for Everyday Agents** — this is specialized, active, judgment-heavy investigative work, not passive background monitoring of personal/household tasks.

## 10. Judging Criteria Alignment

| Criterion | How this project addresses it |
|---|---|
| **Technological Implementation** | Multi-agent orchestration (not single tool-chain), real API integrations, iterative refinement loop, optional AgentCore deployment |
| **Design** | Coherent end-to-end product: input a mutation → get a ranked, cited, scored hypothesis report — not just a raw data dump |
| **Potential Impact** | Addresses a specific, real, named bottleneck (manual literature/database cross-referencing) for a credible, specific audience (oncology researchers) |
| **Creativity & Originality** | Self-critiquing agent loop; transparent synergy scoring; genuine understanding of the research workflow being automated |
| **Presentation** | Demo narrative: show the manual pain point → show autonomous multi-agent cross-referencing → show the system demoting its own top candidate after finding contradicting evidence → land on "researcher's starting point, not a replacement for their judgment" |

## 11. Ethical Framing (also a technical credibility signal)
- All outputs are hypotheses with confidence scores and citations — never recommendations or medical advice.
- The system's ability to distinguish "correlation in literature" from "clinical proof" is itself a demonstration of technical sophistication, not just a disclaimer.

## 12. Deliverables Checklist (per hackathon rules)
- [ ] Text description (what/who/how)
- [ ] Public repo URL, MIT or Apache license visible in About section
- [ ] All source code, assets, setup instructions
- [ ] README
- [ ] Architecture diagram (can reuse knowledge graph visualization if built)
- [ ] Demo video (≤5 min): problem → audience → why it matters → live demo
- [ ] AWS Builder ID
- [ ] Optional: live demo link
- [ ] Optional bonus: builder.aws.com build-journey post, title includes "Agents for Humans"