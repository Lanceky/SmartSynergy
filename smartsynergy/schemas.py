"""Data contracts for SmartSynergy.

Two families of models:
  * Stored records  - what the application persists (SourceRecord, EvidenceClaim, Dossier, ...)
  * Agent outputs   - what the LLM is asked to fill via `structured_output_model`

A valid Pydantic object only proves schema conformity. Citation and evidence rules are
enforced separately in gates.py.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------------------
# Controlled vocabularies. Absent information is a state, never an empty string.
# --------------------------------------------------------------------------------------

Label = Literal["supports", "opposes", "unclear"]
StudyDesign = Literal[
    "cell_line", "animal", "observational", "interventional", "review", "not_reported"
]
ContextMatch = Literal[
    "direct", "indirect_other_tumor", "indirect_other_variant", "not_reported"
]
AccessLevel = Literal["open_access", "abstract_only", "metadata_only"]
Coverage = Literal["complete", "truncated", "failed", "not_retrieved", "not_applicable"]
ChangeReason = Literal[
    "new_counterevidence", "context_mismatch", "source_corrected", "new_direct_evidence"
]
RunStatus = Literal["running", "completed", "failed", "failed_gate", "failed_stale"]
RunTrigger = Literal["scheduled", "manual", "replay"]
ReviewState = Literal["open", "acknowledged", "dismissed", "acted_on"]

DISCLAIMER = "Research hypothesis for expert review; not medical advice."


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------
# Watch configuration
# --------------------------------------------------------------------------------------


class Hypothesis(BaseModel):
    """One curated combination under watch. The panel is curator-selected, not agent-invented."""

    id: str
    question: str
    compound_a: str
    compound_b: str
    compound_a_cid: int | None = None
    compound_b_cid: int | None = None
    compound_a_synonyms: list[str] = Field(default_factory=list)
    compound_b_synonyms: list[str] = Field(default_factory=list)
    rationale: str = ""
    anchor_source_ids: list[str] = Field(default_factory=list)


class Watch(BaseModel):
    id: str
    gene: str
    variant: str
    disease: str
    disease_synonyms: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis]
    schedule_cron: str = "0 6 * * *"
    enabled: bool = True
    policy_version: str = "1"
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------------------
# Retrieved sources
# --------------------------------------------------------------------------------------


class SourceRecord(BaseModel):
    """A publication as retrieved. `content_hash` covers only the text we actually stored."""

    source_id: str  # canonical: PMID when available, else PMCID/DOI
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    title: str
    journal: str | None = None
    year: str | None = None
    first_index_date: str | None = None
    url: str
    access_level: AccessLevel = "metadata_only"
    license: str | None = None
    pub_types: list[str] = Field(default_factory=list)
    cited_by_count: int = 0
    abstract: str | None = None
    is_corrected: bool | None = None  # None = coverage unknown, not "clean"
    retrieved_at: datetime = Field(default_factory=utcnow)
    content_hash: str = ""

    def compute_hash(self) -> str:
        payload = f"{self.source_id}|{self.title}|{self.abstract or ''}|{self.is_corrected}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @property
    def redistributable(self) -> bool:
        """Conservative: only permissive CC-BY / CC0 text may be committed to the repo.

        NC/ND variants are treated as non-redistributable here to avoid any doubt.
        """
        lic = (self.license or "").lower().replace("-", " ")
        if not lic or "nc" in lic.split() or "nd" in lic.split():
            return False
        return any(t in lic for t in ("cc by", "cc0", "public domain"))


# --------------------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------------------


class EvidenceClaimDraft(BaseModel):
    """What the Analyst agent produces. Not trusted until it passes the evidence gate."""

    source_id: str = Field(description="Exact source_id from tool output. Never invent one.")
    finding: str = Field(description="What this source reports about the pair, in one sentence.")
    supporting_passage: str = Field(
        default="",
        description="Verbatim sentence copied from the retrieved text. Empty if unavailable.",
    )
    section: str = Field(default="abstract", description="Where the passage came from.")
    label: Label = Field(description="Does the source support or oppose the combination?")
    context_match: ContextMatch = Field(
        description="direct only if the SOURCE studies this gene+variant+disease."
    )
    study_design: StudyDesign = "not_reported"
    comparator: str = Field(default="not_reported", description="What it was compared against.")
    outcome: str = Field(default="not_reported", description="Reported endpoint/metric.")
    limitations: str = Field(default="not_reported", description="Limits the source itself states.")


class EvidenceClaim(EvidenceClaimDraft):
    """A draft that has been through the gate and persisted."""

    claim_id: str
    hypothesis_id: str
    run_id: str
    passage_verified: bool = False
    challenged_by: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    def fingerprint_part(self) -> str:
        """Prose-independent identity used for change detection."""
        return "|".join(
            [
                self.hypothesis_id,
                self.source_id,
                self.label,
                self.context_match,
                self.study_design,
                "1" if self.passage_verified else "0",
            ]
        )


class Challenge(BaseModel):
    """A Critic objection. Must cite a source; 'I doubt it' is not evidence."""

    target_source_id: str = Field(description="The claim's source_id being challenged.")
    issue: Literal[
        "opposing_result", "context_mismatch", "weak_design", "stated_limitation", "possible_correction"
    ]
    explanation: str
    citing_source_id: str = Field(
        default="", description="Source backing the challenge. May equal target for stated limitations."
    )


# --------------------------------------------------------------------------------------
# Agent output schemas (structured_output_model)
# --------------------------------------------------------------------------------------


class AnalystOutput(BaseModel):
    """Evidence Analyst result."""

    claims: list[EvidenceClaimDraft] = Field(default_factory=list)
    gaps: list[str] = Field(
        default_factory=list, description="What you could NOT determine from retrieved sources."
    )
    searched_queries: list[str] = Field(default_factory=list)


class CriticOutput(BaseModel):
    """Contradiction Critic result. 'No issues' is a legitimate, honest answer."""

    verdict: Literal["no_issues_found_in_scope", "issues_found"]
    challenges: list[Challenge] = Field(default_factory=list)
    followup_queries: list[str] = Field(
        default_factory=list, description="Searches that would resolve a genuine open question."
    )
    searched_queries: list[str] = Field(default_factory=list)


class EditorOutput(BaseModel):
    """Briefing Editor result. References accepted claim_ids only; no new science."""

    brief_markdown: str = Field(
        description="Concise brief. Cite accepted claims inline as [claim:ID]."
    )
    cited_claim_ids: list[str] = Field(default_factory=list)
    headline: str = Field(default="", description="One sentence: the state of this hypothesis.")


# --------------------------------------------------------------------------------------
# Dossiers, runs, review
# --------------------------------------------------------------------------------------


class EvidenceProfile(BaseModel):
    """Separate dimensions, deliberately NOT collapsed into one 'synergy score'."""

    direct_support: int = 0
    indirect_support: int = 0
    opposing: int = 0
    unclear: int = 0
    unverified: int = 0
    strongest_design: StudyDesign = "not_reported"
    independent_sources: int = 0
    open_gaps: list[str] = Field(default_factory=list)

    @property
    def state(self) -> str:
        """Derive an assessment state. Order matters, and it is the whole point.

        Two orderings are deliberate:

        1. Opposing evidence is considered BEFORE the "nothing found" branch. A single
           opposing result with no support is evidence of *harm*, not absence of
           evidence, and reporting it as `insufficient_evidence` would erase the
           distinction this product exists to preserve.
        2. `supported_in_scope` requires DIRECT support. Support that is only indirect
           - a result in another tumour type or another variant - is reported as
           `indirect_support_only`. Calling a colorectal result "supported in scope"
           for a lung hypothesis is precisely the mistake the critic demotes claims to
           prevent; the profile must not reintroduce it.
        """
        if self.opposing and self.direct_support:
            return "conflicting_evidence"
        if self.opposing:
            return "needs_review"
        if self.direct_support == 0 and self.indirect_support == 0:
            return "insufficient_evidence"
        if self.strongest_design in ("cell_line", "animal"):
            return "preclinical_only"
        if self.direct_support == 0:
            return "indirect_support_only"
        return "supported_in_scope"


class Dossier(BaseModel):
    id: str
    version: int
    watch_id: str
    hypothesis_id: str
    run_id: str
    claim_ids: list[str] = Field(default_factory=list)
    profile: EvidenceProfile = Field(default_factory=EvidenceProfile)
    headline: str = ""
    brief_markdown: str = ""
    coverage: Coverage = "complete"
    coverage_note: str = ""
    fingerprint: str = ""
    created_at: datetime = Field(default_factory=utcnow)

    @staticmethod
    def compute_fingerprint(claims: list[EvidenceClaim]) -> str:
        """Order-, prose-, and timestamp-independent. Same evidence => same fingerprint."""
        parts = sorted(c.fingerprint_part() for c in claims)
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


class Run(BaseModel):
    id: str
    watch_id: str
    trigger: RunTrigger
    status: RunStatus = "running"
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    window_start: str | None = None
    window_end: str | None = None
    papers_seen: int = 0
    papers_new: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    coverage: Coverage = "complete"
    source_mode: str = "live"
    model_id: str = ""
    error: str | None = None
    node_events: list[str] = Field(default_factory=list)


class ReviewItem(BaseModel):
    id: str
    watch_id: str
    hypothesis_id: str
    run_id: str
    reason: ChangeReason
    headline: str
    explanation: str
    before_version: int | None = None
    after_version: int = 1
    new_source_ids: list[str] = Field(default_factory=list)
    fingerprint_after: str = ""
    state: ReviewState = "open"
    decided_at: datetime | None = None
    decision_note: str = ""
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def urgency(self) -> int:
        """Inbox ordering only; higher is more urgent. Not a quality or efficacy score.

        Evidence that something is WRONG outranks evidence that something is promising,
        because acting on a retracted or misapplied result is the costlier mistake.
        """
        return {
            "new_counterevidence": 4,
            "source_corrected": 3,
            "context_mismatch": 2,
            "new_direct_evidence": 1,
        }[self.reason]


class RunSummary(BaseModel):
    """Returned by the controller; also the AgentCore entrypoint payload."""

    run_id: str
    watch_id: str
    status: RunStatus
    trigger: RunTrigger
    hypotheses_checked: int = 0
    papers_new: int = 0
    review_items_created: int = 0
    dossier_versions: dict[str, int] = Field(default_factory=dict)
    coverage: Coverage = "complete"
    duration_s: float = 0.0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
