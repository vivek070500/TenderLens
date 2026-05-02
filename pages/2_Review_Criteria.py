import streamlit as st
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import (
    init_db, get_criteria, save_criteria, confirm_criteria,
    get_all_tenders, log_audit,
)

init_db()

st.set_page_config(page_title="Review Criteria - TenderLens", layout="wide")
st.title("Step 2: Review Extracted Criteria")

# ── Select tender ──
tender_id = st.session_state.get("active_tender_id")
tenders = get_all_tenders()

if not tenders:
    st.warning("No tenders found. Please upload a tender document in Step 1.")
    st.stop()

tender_options = {t["id"]: f"{t['name']} ({t['created_at']})" for t in tenders}
selected_id = st.selectbox(
    "Select Tender",
    options=list(tender_options.keys()),
    format_func=lambda x: tender_options[x],
    index=0 if tender_id not in tender_options else list(tender_options.keys()).index(tender_id),
)
st.session_state["active_tender_id"] = selected_id

# ── Load criteria ──
criteria = get_criteria(selected_id)

if not criteria:
    st.warning("No criteria found for this tender. Please process the tender in Step 1.")
    st.stop()

is_confirmed = all(c["confirmed"] for c in criteria)

if is_confirmed:
    st.success("Criteria have been confirmed. You can proceed to Step 3: Evaluation.")

st.markdown(f"**{len(criteria)} criteria extracted.** Review and edit below, then confirm.")
st.markdown("---")

# ── Editable criteria table ──
edited_criteria = []

for i, c in enumerate(criteria):
    with st.expander(
        f"{'✅' if c['confirmed'] else '📝'} {c['criterion_id']} — {c['description'][:80]}...",
        expanded=not is_confirmed,
    ):
        col1, col2 = st.columns([3, 1])

        with col1:
            desc = st.text_area(
                "Description", value=c["description"],
                key=f"desc_{i}", height=80,
            )
            threshold = st.text_input(
                "Threshold / Condition", value=c.get("threshold", ""),
                key=f"thresh_{i}",
            )
            evidence = st.text_input(
                "Expected Evidence", value=c.get("expected_evidence", ""),
                key=f"evid_{i}",
            )

        with col2:
            category = st.selectbox(
                "Category",
                options=["financial", "experience", "compliance", "technical", "other"],
                index=["financial", "experience", "compliance", "technical", "other"].index(
                    c.get("category", "other")
                ) if c.get("category", "other") in ["financial", "experience", "compliance", "technical", "other"] else 4,
                key=f"cat_{i}",
            )
            mandatory = st.checkbox(
                "Mandatory", value=bool(c.get("mandatory", True)),
                key=f"mand_{i}",
            )
            source = st.text_input(
                "Source Section", value=c.get("source_section", ""),
                key=f"src_{i}",
            )

        edited_criteria.append({
            "criterion_id": c["criterion_id"],
            "description": desc,
            "category": category,
            "mandatory": mandatory,
            "threshold": threshold,
            "expected_evidence": evidence,
            "source_section": source,
        })

# ── Add new criterion ──
st.markdown("---")
with st.expander("Add a New Criterion"):
    new_id = st.text_input("Criterion ID", value=f"C-{len(criteria)+1:03d}", key="new_id")
    new_desc = st.text_area("Description", key="new_desc")
    new_cat = st.selectbox("Category", ["financial", "experience", "compliance", "technical"], key="new_cat")
    new_mand = st.checkbox("Mandatory", value=True, key="new_mand")
    new_thresh = st.text_input("Threshold", key="new_thresh")
    new_evid = st.text_input("Expected Evidence", key="new_evid")
    new_src = st.text_input("Source Section", key="new_src")

    if st.button("Add Criterion"):
        if new_desc:
            edited_criteria.append({
                "criterion_id": new_id,
                "description": new_desc,
                "category": new_cat,
                "mandatory": new_mand,
                "threshold": new_thresh,
                "expected_evidence": new_evid,
                "source_section": new_src,
            })
            save_criteria(selected_id, edited_criteria)
            log_audit(selected_id, "criterion_added", f"Added: {new_id} — {new_desc[:60]}")
            st.success(f"Criterion {new_id} added!")
            st.rerun()
        else:
            st.warning("Please enter a description.")

# ── Save and Confirm buttons ──
st.markdown("---")
col_save, col_confirm = st.columns(2)

with col_save:
    if st.button("Save Changes", type="secondary"):
        save_criteria(selected_id, edited_criteria)
        st.success("Changes saved!")
        st.rerun()

with col_confirm:
    if st.button("Confirm Criteria & Proceed to Evaluation", type="primary"):
        save_criteria(selected_id, edited_criteria)
        confirm_criteria(selected_id)
        st.success("Criteria confirmed! Proceed to Step 3: Evaluation.")
        st.rerun()
