import html
import os
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import VERDICT_ELIGIBLE, VERDICT_NOT_ELIGIBLE, VERDICT_NEEDS_REVIEW
from database.db import (
    init_db,
    get_all_tenders,
    get_all_verdicts,
    get_bidders,
    get_criteria,
    get_overrides,
)
from modules.evaluator import compute_overall_verdict
from modules.reporter import generate_pdf_report
from modules.session_workspace import maybe_bind_ephemeral_session
from modules.ui_theme import apply_theme, page_kicker_step

st.set_page_config(page_title="Report | TenderLens", layout="wide")
init_db()
maybe_bind_ephemeral_session()

apply_theme()
page_kicker_step("Step 4 · Reporting")

st.title("Eligibility report")

tender_id = st.session_state.get("active_tender_id")
tenders = get_all_tenders()

if not tenders:
    st.warning("No tenders found. Start from document intake.")
    st.stop()

tender_options = {t["id"]: t["name"] for t in tenders}
if len(tenders) > 1:
    c_sel_a, c_sel_b = st.columns([0.22, 0.78])
    with c_sel_a:
        st.markdown('<p class="tl-section-label" style="margin-top:0.5rem">Tender</p>', unsafe_allow_html=True)
    with c_sel_b:
        selected_id = st.selectbox(
            "Tender",
            options=list(tender_options.keys()),
            format_func=lambda x: tender_options[x],
            index=(
                0
                if tender_id not in tender_options
                else list(tender_options.keys()).index(tender_id)
            ),
            label_visibility="collapsed",
            key="report_tender_select",
        )
else:
    selected_id = tenders[0]["id"]

st.session_state["active_tender_id"] = selected_id

tender = next((t for t in tenders if t["id"] == selected_id), None)
criteria = get_criteria(selected_id)
bidders = get_bidders(selected_id)
all_verdicts = get_all_verdicts(selected_id)
overrides = get_overrides(selected_id)

if not all_verdicts:
    st.warning("No evaluation results yet. Complete the evaluation step first.")
    st.stop()

mandatory_n = sum(1 for c in criteria if c["mandatory"])
generated = datetime.now().strftime("%d %B %Y · %H:%M")

st.markdown(
    f"""
<div class="tl-report-hero">
  <h2 class="tl-hero-title">{html.escape(tender["name"])}</h2>
  <p class="tl-hero-sub">Generated {html.escape(generated)}</p>
  <div class="tl-kpi-row">
    <div class="tl-kpi"><div class="tl-kpi-val">{len(criteria)}</div><div class="tl-kpi-lab">Criteria</div></div>
    <div class="tl-kpi"><div class="tl-kpi-val">{mandatory_n}</div><div class="tl-kpi-lab">Mandatory</div></div>
    <div class="tl-kpi"><div class="tl-kpi-val">{len(bidders)}</div><div class="tl-kpi-lab">Bidders</div></div>
    <div class="tl-kpi"><div class="tl-kpi-val">{len(all_verdicts)}</div><div class="tl-kpi-lab">Assessments</div></div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown('<p class="tl-section-label">Summary by bidder</p>', unsafe_allow_html=True)

bidder_names = list(dict.fromkeys(v["bidder_name"] for v in all_verdicts))
summary_data = []

for bname in bidder_names:
    bverdicts = [v for v in all_verdicts if v["bidder_name"] == bname]
    overall = compute_overall_verdict(bverdicts)
    summary_data.append({
        "Organisation": bname,
        "Passed": sum(1 for v in bverdicts if v["verdict"] == VERDICT_ELIGIBLE),
        "Not met": sum(1 for v in bverdicts if v["verdict"] == VERDICT_NOT_ELIGIBLE),
        "Review": sum(1 for v in bverdicts if v["verdict"] == VERDICT_NEEDS_REVIEW),
        "Outcome": overall.replace("_", " ").title(),
    })

df = pd.DataFrame(summary_data)

overall_counts = {}
for row in summary_data:
    overall_counts[row["Outcome"]] = overall_counts.get(row["Outcome"], 0) + 1

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("Eligible (overall)", overall_counts.get("Eligible", 0))
with m2:
    st.metric("Not eligible (overall)", overall_counts.get("Not Eligible", 0))
with m3:
    st.metric("Needs review (overall)", overall_counts.get("Needs Review", 0))

st.dataframe(
    df,
    width="stretch",
    hide_index=True,
    column_config={
        "Organisation": st.column_config.TextColumn("Organisation", width="large"),
        "Passed": st.column_config.NumberColumn("Passed", format="%d", width="small"),
        "Not met": st.column_config.NumberColumn("Not met", format="%d", width="small"),
        "Review": st.column_config.NumberColumn("Review", format="%d", width="small"),
        "Outcome": st.column_config.TextColumn("Outcome", width="medium"),
    },
)

st.markdown("---")
st.markdown('<p class="tl-section-label">Detail by organisation</p>', unsafe_allow_html=True)


def verdict_icon(vs):
    if vs == VERDICT_ELIGIBLE:
        return "✓", "pass"
    if vs == VERDICT_NOT_ELIGIBLE:
        return "✗", "fail"
    return "◆", "review"


for bname in bidder_names:
    bverdicts = [v for v in all_verdicts if v["bidder_name"] == bname]
    overall = compute_overall_verdict(bverdicts)
    oicon, _ = verdict_icon(overall)
    label = f"{oicon}  {bname}"
    with st.expander(label, expanded=False):
        for v in bverdicts:
            vi, cls = verdict_icon(v["verdict"])
            desc = html.escape(v.get("crit_desc") or "")
            expl = html.escape(v.get("explanation") or "")
            code = html.escape(str(v.get("crit_code") or ""))
            st.markdown(
                f'<div class="tl-criterion-block {cls}"><strong>{vi} {code}</strong> — {desc}'
                f'<div style="font-size:0.88rem;color:#475569;margin-top:0.4rem;line-height:1.45">{expl}</div></div>',
                unsafe_allow_html=True,
            )

if overrides:
    st.markdown("---")
    st.markdown('<p class="tl-section-label">Officer overrides</p>', unsafe_allow_html=True)
    for o in overrides:
        st.markdown(
            f"**{html.escape(o['bidder_name'])}** / {html.escape(str(o['crit_code']))}: "
            f"{html.escape(str(o['original_verdict']))} → **{html.escape(str(o['new_verdict']))}**"
        )
        st.caption(
            f"Reason: {html.escape(o.get('reason') or '')} — "
            f"Officer: {html.escape(str(o.get('officer_name') or 'N/A'))}"
        )

st.markdown("---")
st.markdown('<p class="tl-section-label">Export</p>', unsafe_allow_html=True)
with st.container(border=True):
    ec1, ec2 = st.columns(2)
    with ec1:
        if st.button("Build PDF", type="primary", width="stretch", key="gen_pdf"):
            with st.spinner("Building PDF…"):
                pdf_bytes = generate_pdf_report(
                    tender=tender,
                    criteria=criteria,
                    bidders=bidders,
                    all_verdicts=all_verdicts,
                    overrides=overrides,
                )
            st.session_state["pdf_report_bytes"] = pdf_bytes
            safe_fn = tender["name"].replace("/", "_").replace("\\", "_")
            st.session_state["pdf_report_name"] = f"TenderLens_Report_{safe_fn}.pdf"
            st.rerun()
    with ec2:
        if st.session_state.get("pdf_report_bytes"):
            st.download_button(
                label="Download PDF",
                data=st.session_state["pdf_report_bytes"],
                file_name=st.session_state.get("pdf_report_name", "TenderLens_Report.pdf"),
                mime="application/pdf",
                type="primary",
                width="stretch",
                key="dl_pdf",
            )
        else:
            st.caption("Generate the PDF first, then download.")

st.markdown("---")
col_prev, col_new = st.columns(2)
with col_prev:
    if st.button("← Evaluation", type="secondary", width="stretch", key="nav_prev"):
        st.switch_page("pages/3_Evaluation.py")
with col_new:
    if st.button("Document intake", type="secondary", width="stretch", key="nav_new"):
        st.switch_page("pages/1_Upload_Documents.py")
