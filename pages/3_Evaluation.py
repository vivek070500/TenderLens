import streamlit as st
import os
import sys
import json
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import VERDICT_ELIGIBLE, VERDICT_NOT_ELIGIBLE, VERDICT_NEEDS_REVIEW
from database.db import (
    init_db, get_criteria, get_bidders, get_documents,
    get_all_tenders, save_evidence, save_verdict,
    get_all_verdicts, get_verdicts, log_audit,
    save_override, get_evidence,
)
from modules.bidder_processor import (
    classify_document, extract_evidence_for_criterion,
)
from modules.evaluator import evaluate_criterion, compute_overall_verdict

init_db()

st.set_page_config(page_title="Evaluation - TenderLens", layout="wide")
st.title("Step 3: Bidder Evaluation")

# ── Select tender ──
tender_id = st.session_state.get("active_tender_id")
tenders = get_all_tenders()

if not tenders:
    st.warning("No tenders found. Start from Step 1.")
    st.stop()

tender_options = {t["id"]: f"{t['name']}" for t in tenders}
selected_id = st.selectbox(
    "Select Tender",
    options=list(tender_options.keys()),
    format_func=lambda x: tender_options[x],
    index=0 if tender_id not in tender_options else list(tender_options.keys()).index(tender_id),
)
st.session_state["active_tender_id"] = selected_id

criteria = get_criteria(selected_id)
bidders = get_bidders(selected_id)

if not criteria:
    st.warning("No criteria found. Complete Step 2 first.")
    st.stop()

if not bidders:
    st.warning("No bidders found. Upload bidder documents in Step 1.")
    st.stop()

if not all(c["confirmed"] for c in criteria):
    st.warning("Criteria have not been confirmed yet. Please confirm in Step 2.")

# ── Run Evaluation ──
existing_verdicts = get_all_verdicts(selected_id)

if not existing_verdicts:
    st.info(f"Ready to evaluate {len(bidders)} bidders against {len(criteria)} criteria.")

    if st.button("Run Evaluation", type="primary"):
        total_steps = len(bidders) * len(criteria)
        progress = st.progress(0, text="Starting evaluation...")
        step = 0

        for bidder in bidders:
            docs = get_documents(bidder["id"])

            for crit in criteria:
                step += 1
                pct = int(100 * step / total_steps)
                progress.progress(
                    min(pct, 99),
                    text=f"Evaluating {bidder['name']}: {crit['criterion_id']}..."
                )

                evidence_list = []
                for doc in docs:
                    if not doc["full_text"]:
                        continue

                    ev = extract_evidence_for_criterion(
                        document_text=doc["full_text"],
                        doc_filename=doc["filename"],
                        doc_category=doc.get("doc_category", "unknown"),
                        criterion=crit,
                    )

                    if ev["found"]:
                        ev["source_document"] = doc["filename"]
                        ev["ocr_confidence"] = doc.get("min_ocr_confidence")
                        evidence_list.append(ev)

                        save_evidence(
                            bidder_id=bidder["id"],
                            criterion_id=crit["id"],
                            extracted_value=str(ev.get("extracted_value", "")),
                            raw_text=ev.get("source_text", ""),
                            source_document=doc["filename"],
                            source_page=ev.get("page_reference"),
                            confidence=ev.get("ocr_confidence"),
                            notes=ev.get("notes", ""),
                        )

                verdict = evaluate_criterion(crit, evidence_list)

                save_verdict(
                    bidder_id=bidder["id"],
                    criterion_id=crit["id"],
                    verdict=verdict["verdict"],
                    explanation=verdict["explanation"],
                    confidence=verdict.get("confidence"),
                )

            log_audit(
                selected_id, "bidder_evaluated",
                f"Bidder: {bidder['name']}"
            )

        progress.progress(100, text="Evaluation complete!")
        st.success("All bidders evaluated!")
        st.rerun()

# ── Display Results ──
all_verdicts = get_all_verdicts(selected_id)

if all_verdicts:
    st.markdown("---")
    st.subheader("Evaluation Results")

    # Build the matrix
    verdict_map = {}
    for v in all_verdicts:
        key = (v["bidder_name"], v["crit_code"])
        verdict_map[key] = v

    bidder_names = list(dict.fromkeys(v["bidder_name"] for v in all_verdicts))
    crit_codes = list(dict.fromkeys(v["crit_code"] for v in all_verdicts))

    def verdict_icon(verdict_str):
        if verdict_str == VERDICT_ELIGIBLE:
            return "✅"
        elif verdict_str == VERDICT_NOT_ELIGIBLE:
            return "❌"
        else:
            return "⚠️"

    # Summary table
    matrix_data = []
    for bname in bidder_names:
        row = {"Bidder": bname}
        bidder_verdicts = []
        for cc in crit_codes:
            v = verdict_map.get((bname, cc))
            if v:
                row[cc] = verdict_icon(v["verdict"])
                bidder_verdicts.append(v)
        overall = compute_overall_verdict(bidder_verdicts) if bidder_verdicts else "N/A"
        row["Overall"] = verdict_icon(overall) + " " + overall.replace("_", " ").title()
        matrix_data.append(row)

    df = pd.DataFrame(matrix_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Summary counts
    overall_counts = {}
    for bname in bidder_names:
        bverdicts = [v for v in all_verdicts if v["bidder_name"] == bname]
        ov = compute_overall_verdict(bverdicts)
        overall_counts[ov] = overall_counts.get(ov, 0) + 1

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Eligible", overall_counts.get(VERDICT_ELIGIBLE, 0))
    with col2:
        st.metric("Not Eligible", overall_counts.get(VERDICT_NOT_ELIGIBLE, 0))
    with col3:
        st.metric("Needs Review", overall_counts.get(VERDICT_NEEDS_REVIEW, 0))

    # ── Detailed view per bidder ──
    st.markdown("---")
    st.subheader("Detailed Evaluation")

    filter_opt = st.selectbox(
        "Filter bidders",
        ["All", "Eligible Only", "Not Eligible Only", "Needs Review Only"],
    )

    for bname in bidder_names:
        bverdicts = [v for v in all_verdicts if v["bidder_name"] == bname]
        overall = compute_overall_verdict(bverdicts)

        if filter_opt == "Eligible Only" and overall != VERDICT_ELIGIBLE:
            continue
        if filter_opt == "Not Eligible Only" and overall != VERDICT_NOT_ELIGIBLE:
            continue
        if filter_opt == "Needs Review Only" and overall != VERDICT_NEEDS_REVIEW:
            continue

        icon = verdict_icon(overall)
        with st.expander(f"{icon} {bname} — {overall.replace('_', ' ').title()}"):
            for v in bverdicts:
                vi = verdict_icon(v["verdict"])
                mand = "Mandatory" if v.get("mandatory", True) else "Optional"
                st.markdown(
                    f"**{vi} {v['crit_code']}** ({v['category']}, {mand}): "
                    f"{v['crit_desc']}"
                )
                st.markdown(f"> {v['explanation']}")

                # Officer override
                bid_id = None
                for b in bidders:
                    if b["name"] == bname:
                        bid_id = b["id"]
                        break

                if v["verdict"] == VERDICT_NEEDS_REVIEW and bid_id:
                    with st.form(key=f"override_{v['id']}"):
                        st.markdown("**Officer Override:**")
                        new_v = st.selectbox(
                            "New Verdict",
                            [VERDICT_ELIGIBLE, VERDICT_NOT_ELIGIBLE],
                            key=f"nv_{v['id']}",
                        )
                        reason = st.text_input("Reason for override", key=f"reason_{v['id']}")
                        officer = st.text_input("Officer Name", key=f"officer_{v['id']}")

                        if st.form_submit_button("Submit Override"):
                            if reason:
                                save_override(
                                    verdict_id=v["id"],
                                    original_verdict=v["verdict"],
                                    new_verdict=new_v,
                                    reason=reason,
                                    officer_name=officer,
                                )
                                log_audit(
                                    selected_id, "officer_override",
                                    f"{bname} / {v['crit_code']}: {v['verdict']} -> {new_v}. Reason: {reason}"
                                )
                                st.success("Override saved!")
                                st.rerun()
                            else:
                                st.warning("Please provide a reason.")

                st.markdown("---")
