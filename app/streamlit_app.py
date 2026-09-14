"""SmartSynergy console.

Deliberately not a chat window. The agent works on a schedule; this is where a human
comes to see what it decided was worth their attention, and to check its work.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smartsynergy.config import get_settings  # noqa: E402
from smartsynergy.export import dossier_json, dossier_markdown  # noqa: E402
from smartsynergy.schemas import DISCLAIMER  # noqa: E402
from smartsynergy.storage import Store  # noqa: E402

st.set_page_config(page_title="SmartSynergy", page_icon="🔬", layout="wide")

REASON_LABEL = {
    "new_direct_evidence": "New directly applicable evidence",
    "new_counterevidence": "New evidence against this hypothesis",
    "context_mismatch": "Applicability problem found",
    "source_corrected": "A cited source was corrected or retracted",
    "assessment_flipped": "Assessment changed direction",
}
STATE_STYLE = {
    "conflicting_evidence": ("Conflicting evidence", "#b45309"),
    "insufficient_evidence": ("Insufficient evidence", "#6b7280"),
    "preclinical_only": ("Preclinical only", "#7c3aed"),
    "needs_review": ("Needs review", "#b45309"),
    "supported_in_scope": ("Supported in scope", "#047857"),
    "indirect_support_only": ("Indirect support only", "#0e7490"),
}
CONTEXT_LABEL = {
    "direct": "Direct — same disease and variant",
    "indirect_other_tumor": "Indirect — different tumour type",
    "indirect_other_variant": "Indirect — different variant",
    "not_reported": "Applicability not stated",
}

st.markdown(
    """<style>
    .badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:0.75rem;
           font-weight:600;color:#fff;margin-right:6px}
    .receipt{border-left:3px solid #d1d5db;padding:0.2rem 0 0.2rem 0.9rem;margin:0.4rem 0;
             color:#374151;font-style:italic}
    .muted{color:#6b7280;font-size:0.85rem}
    .card{border:1px solid #e5e7eb;border-radius:10px;padding:1rem 1.2rem;margin-bottom:0.9rem}
    </style>""",
    unsafe_allow_html=True,
)


@st.cache_resource
def store() -> Store:
    return Store(get_settings().db_path)


def badge(text: str, color: str) -> str:
    return f'<span class="badge" style="background:{color}">{text}</span>'


def state_badge(state: str) -> str:
    label, color = STATE_STYLE.get(state, (state, "#6b7280"))
    return badge(label, color)


db = store()
watches = db.list_watches()

with st.sidebar:
    st.title("🔬 SmartSynergy")
    st.caption("A second opinion that never sleeps.")
    if not watches:
        st.warning("No watch configured.")
        st.code("python -m smartsynergy.worker init-watch fixtures/panel.json")
        st.stop()
    watch_id = st.selectbox("Watch", [w.id for w in watches])
    watch = db.get_watch(watch_id)
    st.markdown(f"**{watch.gene} {watch.variant}** · {watch.disease}")
    st.markdown(f"<span class='muted'>Schedule <code>{watch.schedule_cron}</code></span>", unsafe_allow_html=True)

    meta = db.watch_meta(watch_id)
    st.markdown(
        f"<span class='muted'>Last checked: {meta.get('last_checked_at') or 'never'}<br>"
        f"Evidence current to: {(meta.get('watermark') or 'baseline pending')[:10]}</span>",
        unsafe_allow_html=True,
    )
    open_items = db.list_inbox(watch_id)
    st.metric("Awaiting your decision", len(open_items))
    page = st.radio("View", ["Decision inbox", "Hypotheses", "Run history", "How it works"], label_visibility="collapsed")
    st.divider()
    st.caption(DISCLAIMER)


def render_receipts(claims, sources, key_prefix: str) -> None:
    if not claims:
        st.info("No claims survived the evidence gate on this pass.")
        return
    for c in claims:
        src = sources.get(c.source_id)
        title = src.title if src else c.source_id
        direction = {"supports": "🟢 supports", "opposes": "🔴 opposes", "unclear": "⚪ unclear", "mixed": "🟡 mixed"}
        with st.expander(f"{direction.get(c.label, c.label)} · {title[:95]}"):
            st.markdown(f"**Finding.** {c.finding}")
            st.markdown(f'<div class="receipt">“{c.supporting_passage}”</div>', unsafe_allow_html=True)
            cols = st.columns(4)
            cols[0].markdown(f"**Applicability**<br><span class='muted'>{CONTEXT_LABEL.get(c.context_match, c.context_match)}</span>", unsafe_allow_html=True)
            cols[1].markdown(f"**Study design**<br><span class='muted'>{c.study_design}</span>", unsafe_allow_html=True)
            cols[2].markdown(f"**Comparator**<br><span class='muted'>{c.comparator}</span>", unsafe_allow_html=True)
            cols[3].markdown(
                "**Quote check**<br><span class='muted'>"
                + ("✅ found verbatim in source" if c.passage_verified else "⚠️ NOT found — treat with caution")
                + "</span>",
                unsafe_allow_html=True,
            )
            st.markdown(f"**Limitations the authors state.** {c.limitations}")
            if c.challenged_by:
                st.warning("Challenged by the critic: " + "; ".join(c.challenged_by))
            if src:
                st.markdown(
                    f"<span class='muted'>{src.journal or ''} {src.year or ''} · cited by {src.cited_by_count} · "
                    f"{src.access_level.replace('_', ' ')} · licence {src.license or 'not stated'}<br>"
                    f"<a href='{src.url}' target='_blank'>{c.source_id} — open the source</a></span>",
                    unsafe_allow_html=True,
                )


if page == "Decision inbox":
    st.header("Decision inbox")
    st.caption("The agent checked the literature. It only appears here when something changed that you would want to act on.")
    items = db.list_inbox(watch_id)
    if not items:
        st.success("**Inbox zero.** The watch ran and found nothing that changes any assessment.")
        st.caption(
            "This is the intended state. A watch that produces an alert every day is a watch you stop reading — "
            "so repeat findings, rephrasings and already-seen papers are filtered out before they reach you."
        )
        with st.expander("Show resolved items"):
            for it in db.list_inbox(watch_id, state=None):
                if it.state != "open":
                    st.markdown(f"- `{it.state}` · **{it.hypothesis_id}** — {it.headline}")
    for it in items:
        with st.container(border=True):
            urgency_color = "#b91c1c" if it.urgency >= 3 else "#b45309" if it.urgency == 2 else "#6b7280"
            st.markdown(
                badge(REASON_LABEL.get(it.reason, it.reason), urgency_color)
                + f"<span class='muted'>{it.hypothesis_id} · v{it.before_version or '—'} → v{it.after_version}</span>",
                unsafe_allow_html=True,
            )
            st.subheader(it.headline)
            st.markdown(f"**What changed, and why it matters.** {it.explanation}")
            if it.new_source_ids:
                st.markdown("**New sources behind this:** " + ", ".join(f"`{s}`" for s in it.new_source_ids))

            dossier = db.latest_dossier(it.hypothesis_id)
            if dossier:
                claims = db.get_claims(dossier.claim_ids)
                sources = db.get_sources(c.source_id for c in claims)
                with st.expander("Check the agent's work — every claim, with its verbatim source passage"):
                    render_receipts(claims, sources, it.id)

            c1, c2, c3 = st.columns(3)
            if c1.button("Acknowledge", key=f"ack{it.id}"):
                db.record_decision(it.id, "acknowledged"); st.rerun()
            if c2.button("Act on this", key=f"act{it.id}"):
                db.record_decision(it.id, "acted_on"); st.rerun()
            if c3.button("Dismiss", key=f"dis{it.id}"):
                db.record_decision(it.id, "dismissed"); st.rerun()

elif page == "Hypotheses":
    st.header("Hypotheses under watch")
    for hyp in watch.hypotheses:
        dossier = db.latest_dossier(hyp.id)
        with st.container(border=True):
            if dossier:
                st.markdown(state_badge(dossier.profile.state) + f"<span class='muted'>v{dossier.version} · {dossier.created_at:%Y-%m-%d %H:%M} UTC</span>", unsafe_allow_html=True)
            else:
                st.markdown(badge("Not yet assessed", "#6b7280"), unsafe_allow_html=True)
            st.subheader(f"{hyp.compound_a} + {hyp.compound_b}")
            st.markdown(hyp.question)
            if hyp.rationale:
                st.markdown(f"<span class='muted'>{hyp.rationale}</span>", unsafe_allow_html=True)

            if not dossier:
                st.code(f"python -m smartsynergy.worker run-watch {watch_id} --hypothesis {hyp.id}")
                continue

            p = dossier.profile
            m = st.columns(5)
            m[0].metric("Direct support", p.direct_support)
            m[1].metric("Indirect", p.indirect_support)
            m[2].metric("Opposing", p.opposing)
            m[3].metric("Independent sources", p.independent_sources)
            m[4].metric("Strongest design", p.strongest_design.replace("_", " "))
            st.caption(
                "These stay separate on purpose. Collapsing them into one 'synergy score' would hide "
                "the difference between *no evidence* and *evidence of harm*."
            )

            if dossier.brief_markdown:
                st.markdown(dossier.brief_markdown)
            if p.open_gaps:
                st.markdown("**What would change this assessment**")
                for g in p.open_gaps:
                    st.markdown(f"- {g}")
            if dossier.coverage != "complete":
                st.warning(f"Coverage was **{dossier.coverage}** on this run — {dossier.coverage_note}. Treat as partial.")

            claims = db.get_claims(dossier.claim_ids)
            sources = db.get_sources(c.source_id for c in claims)
            st.markdown("#### Evidence receipts")
            render_receipts(claims, sources, hyp.id)

            versions = db.dossier_versions(hyp.id)
            if len(versions) > 1:
                st.markdown("#### History")
                for v in versions:
                    st.markdown(f"- **v{v.version}** · {v.created_at:%Y-%m-%d %H:%M} · `{v.profile.state}` · fingerprint `{v.fingerprint}`")

            question = hyp.question
            d1, d2 = st.columns(2)
            d1.download_button("Download Markdown", dossier_markdown(dossier, claims, sources, question),
                               file_name=f"{hyp.id}-v{dossier.version}.md", key=f"md{hyp.id}")
            d2.download_button("Download JSON", dossier_json(dossier, claims, sources),
                               file_name=f"{hyp.id}-v{dossier.version}.json", key=f"js{hyp.id}")

elif page == "Run history":
    st.header("Run history")
    st.caption("A watch you cannot audit is a watch you cannot trust. Every pass is recorded, including the ones that found nothing and the ones that failed.")
    runs = db.list_runs(watch_id, limit=50)
    if not runs:
        st.info("No runs yet.")
        st.code(f"python -m smartsynergy.worker run-watch {watch_id}")
    for r in runs:
        icon = {"completed": "✅", "failed": "❌", "running": "⏳"}.get(r.status, "•")
        cover = "" if r.coverage == "complete" else f" · ⚠️ coverage {r.coverage}"
        with st.expander(f"{icon} {r.started_at:%Y-%m-%d %H:%M} · {r.trigger} · {r.papers_new} new sources{cover}"):
            c = st.columns(4)
            c[0].metric("Sources seen", r.papers_seen)
            c[1].metric("New", r.papers_new)
            c[2].metric("Tool calls", r.tool_calls)
            c[3].metric("Window", f"{r.window_start or 'baseline'}")
            st.markdown(f"<span class='muted'>Model `{r.model_id}` · run `{r.id}`</span>", unsafe_allow_html=True)
            if r.node_events:
                st.markdown("**Graph path:** " + " → ".join(f"`{n}`" for n in r.node_events))
            if r.error:
                st.error(r.error)
                st.caption("A failed run never advances the evidence watermark, so the next run re-checks the same window rather than silently skipping it.")

else:
    st.header("How it works")
    st.markdown(
        """
SmartSynergy watches a small set of **combination-therapy hypotheses** and tells a researcher
when the literature has actually moved. It is built as a Strands **Graph**, not a chat loop.

**The path every hypothesis takes**

1. **Analyst** searches Europe PMC and PubChem and proposes claims — each one must quote its source verbatim.
2. **Critic** is a separate agent whose only job is to attack those claims: wrong tumour type, wrong variant,
   a retraction, a missing comparator.
3. **Evidence gate** is *not* a model. It is plain Python, and it is where trust is enforced:
   * a claim about a source the agent never actually retrieved is **deleted**, so a fabricated citation cannot reach you
   * a quote that is not a substring of the stored text is flagged `passage_verified = false`
   * a critic finding of `context_mismatch` **forcibly demotes** direct evidence to indirect
   * the gate may send the Analyst back for **exactly one** follow-up pass, then it must decide
4. **Editor** writes the brief, and a final gate rejects it outright if it cites a claim ID that does not exist.

**Why it stays quiet**

Every assessment is reduced to a *fingerprint* over its claims — order-independent and prose-independent.
If tomorrow's run produces the same fingerprint, nothing reaches the inbox. You are interrupted only for
new applicable evidence, new counterevidence, an applicability problem, or a retraction.

**What it will not do**

It will not tell you a drug combination works. It has no access to unpublished trial data, and the
published record is incomplete and biased toward positive results. It reports what the literature says,
where that evidence came from, and — most usefully — where it does not apply.
        """
    )
    st.info(DISCLAIMER)
