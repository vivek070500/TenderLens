import streamlit as st
import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import VERDICT_ELIGIBLE, VERDICT_NOT_ELIGIBLE, VERDICT_NEEDS_REVIEW
from database.db import (
    init_db, get_criteria, get_bidders, get_all_tenders,
    get_all_verdicts, get_audit_log, get_overrides,
)
from modules.evaluator import compute_overall_verdict
from modules.reporter import generate_pdf_report

init_db()

st.set_page_config(page_title="Report - TenderLens", layout="wide")
st.title("Step 4: Consolidated Report")

# ── Select tender ──
tender_id = st.session_state.get("active_tender_id")
tenders = get_all_tenders()

if not tenders:
    st.warning("No tenders found.")
    st.stop()

tender_options = {t["id"]: f"{t['name']}" for t in tenders}
selected_id = st.selectbox(
    "Select Tender",
    options=list(tender_options.keys()),
    format_func=lambda x: tender_options[x],
    index=0 if tender_id not in tender_options else list(tender_options.keys()).index(tender_id),
)

tender = next((t for t in tenders if t["id"] == selected_id), None)
criteria = get_criteria(selected_id)
bidders = get_bidders(selected_id)
all_verdicts = get_all_verdicts(selected_id)
audit_entries = get_audit_log(selected_id)
overrides = get_overrides(selected_id)

if not all_verdicts:
    st.warning("No evaluation results found. Complete Step 3 first.")
    st.stop()

# ── Report Preview ──
st.subheader("Report Preview")

st.markdown(f"**Tender:** {tender['name']}")
st.markdown(f"**Generated:** {datetime.now().strftime('%d %B %Y, %H:%M')}")
st.markdown(f"**Criteria:** {len(criteria)} ({sum(1 for c in criteria if c['mandatory'])} mandatory)")
st.markdown(f"**Bidders:** {len(bidders)}")

st.markdown("---")

# ── Summary ──
st.subheader("Summary")

bidder_names = list(dict.fromkeys(v["bidder_name"] for v in all_verdicts))
summary_data = []

for bname in bidder_names:
    bverdicts = [v for v in all_verdicts if v["bidder_name"] == bname]
    overall = compute_overall_verdict(bverdicts)
    eligible_count = sum(1 for v in bverdicts if v["verdict"] == VERDICT_ELIGIBLE)
    fail_count = sum(1 for v in bverdicts if v["verdict"] == VERDICT_NOT_ELIGIBLE)
    review_count = sum(1 for v in bverdicts if v["verdict"] == VERDICT_NEEDS_REVIEW)
    summary_data.append({
        "Bidder": bname,
        "Eligible Criteria": eligible_count,
        "Failed Criteria": fail_count,
        "Needs Review": review_count,
        "Overall": overall.replace("_", " ").title(),
    })

import pandas as pd
df = pd.DataFrame(summary_data)
st.dataframe(df, use_container_width=True, hide_index=True)

overall_counts = {}
for row in summary_data:
    ov = row["Overall"]
    overall_counts[ov] = overall_counts.get(ov, 0) + 1

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Eligible", overall_counts.get("Eligible", 0))
with col2:
    st.metric("Not Eligible", overall_counts.get("Not Eligible", 0))
with col3:
    st.metric("Needs Review", overall_counts.get("Needs Review", 0))

# ── Per-bidder details ──
st.markdown("---")
st.subheader("Per-Bidder Evaluation Details")

for bname in bidder_names:
    bverdicts = [v for v in all_verdicts if v["bidder_name"] == bname]
    overall = compute_overall_verdict(bverdicts)

    def verdict_icon(vs):
        if vs == VERDICT_ELIGIBLE:
            return "✅"
        elif vs == VERDICT_NOT_ELIGIBLE:
            return "❌"
        return "⚠️"

    with st.expander(f"{verdict_icon(overall)} {bname}"):
        for v in bverdicts:
            vi = verdict_icon(v["verdict"])
            st.markdown(f"**{vi} {v['crit_code']}** — {v['crit_desc']}")
            st.caption(v["explanation"])

# ── Officer Overrides ──
if overrides:
    st.markdown("---")
    st.subheader("Officer Overrides")
    for o in overrides:
        st.markdown(
            f"**{o['bidder_name']}** / {o['crit_code']}: "
            f"{o['original_verdict']} → **{o['new_verdict']}**"
        )
        st.caption(f"Reason: {o['reason']} — Officer: {o.get('officer_name', 'N/A')} — {o['created_at']}")

# ── Audit Trail ──
st.markdown("---")
st.subheader("Audit Trail")
if audit_entries:
    for entry in audit_entries:
        st.caption(f"[{entry['created_at']}] **{entry['action']}** — {entry.get('details', '')}")
else:
    st.info("No audit entries yet.")

# ── Export PDF ──
st.markdown("---")
st.subheader("Export Report")

if st.button("Generate PDF Report", type="primary"):
    with st.spinner("Generating PDF..."):
        pdf_bytes = generate_pdf_report(
            tender=tender,
            criteria=criteria,
            bidders=bidders,
            all_verdicts=all_verdicts,
            overrides=overrides,
            audit_entries=audit_entries,
        )

    st.download_button(
        label="Download PDF Report",
        data=pdf_bytes,
        file_name=f"TenderLens_Report_{tender['name'].replace('/', '_')}.pdf",
        mime="application/pdf",
    )
    st.success("PDF generated!")
