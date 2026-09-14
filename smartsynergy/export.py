"""Export a dossier as a self-contained Markdown or JSON record.

Evidence tables are rendered from the stored ledger, never from model prose, so an
exported file cannot contain a citation the database does not have.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from smartsynergy.schemas import DISCLAIMER, Dossier, EvidenceClaim, SourceRecord

_LABEL = {"supports": "Supports", "opposes": "Opposes", "unclear": "Unclear", "mixed": "Mixed"}
_CONTEXT = {
    "direct": "Direct (same disease + variant)",
    "indirect_other_tumor": "Indirect - other tumour type",
    "indirect_other_variant": "Indirect - other variant",
    "indirect_preclinical": "Indirect - preclinical only",
    "unclear": "Unclear",
}


def _esc(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def dossier_markdown(
    dossier: Dossier,
    claims: list[EvidenceClaim],
    sources: dict[str, SourceRecord],
    hypothesis_question: str = "",
) -> str:
    p = dossier.profile
    out: list[str] = []
    out.append(f"# {dossier.headline or hypothesis_question or dossier.hypothesis_id}")
    out.append("")
    out.append(f"**Hypothesis:** {hypothesis_question or dossier.hypothesis_id}  ")
    out.append(f"**Assessment state:** `{p.state}`  ")
    out.append(f"**Dossier version:** v{dossier.version}  ")
    out.append(f"**Generated:** {dossier.created_at:%Y-%m-%d %H:%M UTC} (run `{dossier.run_id}`)  ")
    out.append(f"**Coverage:** {dossier.coverage} - {dossier.coverage_note}")
    out.append("")
    out.append("## Evidence profile")
    out.append("")
    out.append("| Dimension | Value |")
    out.append("| --- | --- |")
    out.append(f"| Directly applicable support | {p.direct_support} |")
    out.append(f"| Indirect / out-of-context support | {p.indirect_support} |")
    out.append(f"| Opposing evidence | {p.opposing} |")
    out.append(f"| Unclear | {p.unclear} |")
    out.append(f"| Strongest study design | {p.strongest_design} |")
    out.append(f"| Independent sources | {p.independent_sources} |")
    out.append("")
    if p.open_gaps:
        out.append("**Open gaps (what would change this assessment):**")
        out.append("")
        out.extend(f"- {g}" for g in p.open_gaps)
        out.append("")

    out.append("## Brief")
    out.append("")
    out.append(dossier.brief_markdown or "_No brief produced._")
    out.append("")

    out.append("## Evidence ledger")
    out.append("")
    if not claims:
        out.append("_No claims passed the evidence gate._")
    else:
        out.append("| Claim | Source | Finding | Direction | Applicability | Design | Verified quote |")
        out.append("| --- | --- | --- | --- | --- | --- | --- |")
        for c in claims:
            src = sources.get(c.source_id)
            link = f"[{c.source_id}]({src.url})" if src else c.source_id
            out.append(
                f"| `{c.claim_id}` | {link} | {_esc(c.finding)} | {_LABEL.get(c.label, c.label)} | "
                f"{_CONTEXT.get(c.context_match, c.context_match)} | {c.study_design} | "
                f"{'yes' if c.passage_verified else 'NO - unverified'} |"
            )
    out.append("")

    out.append("## Evidence receipts")
    out.append("")
    for c in claims:
        src = sources.get(c.source_id)
        out.append(f"### `{c.claim_id}` - {c.source_id}")
        out.append("")
        if src:
            out.append(f"**{src.title}**  ")
            meta = " | ".join(x for x in [src.journal, src.year, f"cited by {src.cited_by_count}"] if x)
            out.append(f"{meta}  ")
            out.append(f"<{src.url}>  ")
            out.append(f"Access: {src.access_level} | Licence: {src.license or 'not stated'}")
            out.append("")
        out.append(f"> {c.supporting_passage}")
        out.append("")
        out.append(f"- Section: {c.section}")
        out.append(f"- Quote verified against retrieved text: **{'yes' if c.passage_verified else 'NO'}**")
        out.append(f"- Comparator: {c.comparator}")
        out.append(f"- Outcome: {c.outcome}")
        out.append(f"- Stated limitations: {c.limitations}")
        if c.challenged_by:
            out.append(f"- Challenges recorded: {'; '.join(c.challenged_by)}")
        out.append("")

    out.append("---")
    out.append("")
    out.append(f"_{DISCLAIMER}_")
    out.append("")
    out.append(f"_Fingerprint `{dossier.fingerprint}` - identical evidence always produces this same value._")
    return "\n".join(out)


def dossier_json(
    dossier: Dossier, claims: list[EvidenceClaim], sources: dict[str, SourceRecord]
) -> str:
    payload = {
        "dossier": dossier.model_dump(mode="json"),
        "claims": [c.model_dump(mode="json") for c in claims],
        "sources": {k: v.model_dump(mode="json") for k, v in sources.items()},
        "disclaimer": DISCLAIMER,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(payload, indent=2)
