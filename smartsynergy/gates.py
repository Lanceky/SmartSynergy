"""Deterministic evidence gates. No LLM here - this is where trust is actually enforced.

The evidence gate answers: which analyst claims are allowed to exist?
The final gate answers:    is the editor's brief grounded in those claims?
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from smartsynergy.schemas import (
    AnalystOutput,
    Challenge,
    CriticOutput,
    EvidenceClaim,
    EvidenceProfile,
    SourceRecord,
)

_WS = re.compile(r"\s+")
_DESIGN_RANK = {
    "not_reported": 0,
    "review": 1,
    "cell_line": 2,
    "animal": 3,
    "observational": 4,
    "interventional": 5,
}


def _norm(text: str) -> str:
    return _WS.sub(" ", (text or "").lower()).strip()


def _claim_id(hypothesis_id: str, source_id: str, finding: str) -> str:
    digest = hashlib.sha256(f"{hypothesis_id}|{source_id}|{_norm(finding)}".encode()).hexdigest()[:6]
    return f"{hypothesis_id[:8]}-{source_id[-6:]}-{digest}"


@dataclass
class GateResult:
    accepted: list[EvidenceClaim] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (source_id, why)
    gaps: list[str] = field(default_factory=list)
    followup_queries: list[str] = field(default_factory=list)
    needs_followup: bool = False

    @property
    def marker(self) -> str:
        return "FOLLOWUP_REQUIRED" if self.needs_followup else "GATE_PASSED"


def evidence_gate(
    analyst: AnalystOutput,
    critic: CriticOutput,
    *,
    hypothesis_id: str,
    run_id: str,
    known_sources: dict[str, SourceRecord],
    followup_used: bool,
) -> GateResult:
    """Validate analyst claims against what was actually retrieved, then fold in challenges."""
    result = GateResult(gaps=list(analyst.gaps))
    challenges_by_source: dict[str, list[Challenge]] = {}
    for ch in critic.challenges:
        challenges_by_source.setdefault(ch.target_source_id, []).append(ch)

    seen_identity: set[str] = set()

    for draft in analyst.claims:
        rec = known_sources.get(draft.source_id)

        # 1. The source must actually have been retrieved. Blocks invented citations.
        if rec is None:
            result.rejected.append((draft.source_id, "unknown_source: not retrieved in this run"))
            continue

        # 2. Deduplicate the same finding about the same study (PMID/PMCID/DOI aliases).
        canonical = rec.pmid or rec.pmcid or rec.doi or rec.source_id
        identity = f"{canonical}|{_norm(draft.finding)[:80]}"
        if identity in seen_identity:
            result.rejected.append((draft.source_id, "duplicate_claim"))
            continue
        seen_identity.add(identity)

        # 3. The passage must really appear in the stored text. Blocks fabricated quotes.
        stored = _norm(rec.abstract or "")
        passage = _norm(draft.supporting_passage)
        verified = bool(passage) and bool(stored) and passage in stored

        claim = EvidenceClaim(
            **draft.model_dump(),
            claim_id=_claim_id(hypothesis_id, draft.source_id, draft.finding),
            hypothesis_id=hypothesis_id,
            run_id=run_id,
            passage_verified=verified,
        )

        # 4. Critic challenges are applied deterministically, not left to the editor's goodwill.
        for ch in challenges_by_source.get(draft.source_id, []):
            claim.challenged_by.append(f"{ch.issue}: {ch.explanation[:160]}")
            if ch.issue == "opposing_result" and claim.label == "supports":
                claim.label = "unclear"
            elif ch.issue == "context_mismatch" and claim.context_match == "direct":
                claim.context_match = "indirect_other_tumor"

        # 5. A correction/retraction flag on the record can never read as clean support.
        if rec.is_corrected and claim.label == "supports":
            claim.label = "unclear"
            claim.challenged_by.append("possible_correction: record flagged in source metadata")

        if not verified and draft.supporting_passage:
            claim.challenged_by.append("unverified_passage: quote not found in stored text")

        result.accepted.append(claim)

    if not any(c.passage_verified for c in result.accepted):
        result.gaps.append("No claim passage could be verified against retrieved text.")

    result.followup_queries = list(critic.followup_queries)
    result.needs_followup = bool(critic.followup_queries) and not followup_used
    return result


def build_profile(claims: list[EvidenceClaim], gaps: list[str]) -> EvidenceProfile:
    """Separate dimensions. Deliberately never collapsed into a single score.

    Only claims whose quoted passage was verified against the retrieved text are
    counted. An unverified claim stays in the ledger - it is still visible, and still
    auditable - but it must not move the assessment, because nothing establishes that
    the underlying paper actually says it.
    """
    profile = EvidenceProfile(open_gaps=gaps[:6])
    best = "not_reported"
    for c in claims:
        if not c.passage_verified:
            profile.unverified += 1
            continue
        if c.label == "opposes":
            profile.opposing += 1
        elif c.label == "unclear":
            profile.unclear += 1
        elif c.context_match == "direct":
            profile.direct_support += 1
        else:
            profile.indirect_support += 1
        if c.label == "supports" and _DESIGN_RANK[c.study_design] > _DESIGN_RANK[best]:
            best = c.study_design
    profile.strongest_design = best  # type: ignore[assignment]
    profile.independent_sources = len({c.source_id for c in claims if c.passage_verified})
    if profile.unverified:
        profile.open_gaps = (
            profile.open_gaps
            + [f"{profile.unverified} claim(s) could not be verified and were not counted."]
        )[:7]
    return profile


_CLAIM_REF = re.compile(r"\[claim:([^\]]+)\]")


def final_gate(brief_markdown: str, accepted: list[EvidenceClaim]) -> tuple[bool, str, str]:
    """Reject briefs that are not backed by verified, existing evidence.

    Returns (ok, cleaned_markdown, reason).
    """
    valid = {c.claim_id for c in accepted if c.passage_verified}
    referenced = set(_CLAIM_REF.findall(brief_markdown or ""))
    unknown = {r for r in referenced if r not in valid}
    if unknown:
        # Either invented outright, or real but unverifiable. Both are unciteable.
        return False, brief_markdown, f"brief cites unknown or unverified claim ids: {sorted(unknown)[:3]}"
    if not valid and brief_markdown.strip():
        # With nothing verified to cite, any prose is by definition uncited assertion.
        # Without this, a confident sentence backed by zero evidence passes untouched.
        return False, brief_markdown, "brief makes assertions but no verified claim supports them"
    if valid and not referenced:
        return False, brief_markdown, "brief cites no evidence despite verified claims existing"
    return True, brief_markdown, ""
