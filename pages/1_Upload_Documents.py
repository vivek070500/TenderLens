import streamlit as st
import os
import sys
import shutil
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import UPLOADS_DIR, SAMPLE_DATA_DIR
from database.db import (
    init_db, create_tender, create_bidder, save_document,
    save_criteria, log_audit, get_all_tenders,
)
from modules.ingestion import ingest_document
from modules.tender_analyzer import extract_criteria
from modules import llm

init_db()

st.set_page_config(page_title="Upload Documents - TenderLens", layout="wide")
st.title("Step 1: Upload Documents")

# ── Check Ollama ──
if not llm.is_available():
    st.error(
        "Ollama is not running. Please start it with `ollama serve` in a terminal, "
        "then refresh this page."
    )
    st.stop()

st.success("Ollama is connected.")

# ── Tabs: Upload or Use Sample Data ──
tab_upload, tab_sample = st.tabs(["Upload Files", "Use Sample Data"])

# ════════════════════════════════════════
# TAB 1: Manual Upload
# ════════════════════════════════════════
with tab_upload:
    st.subheader("Upload Tender Document")
    tender_name = st.text_input("Tender Name / Number", placeholder="e.g., CRPF/CONST/2026-27/001")
    tender_file = st.file_uploader(
        "Upload the tender document",
        type=["pdf", "txt", "docx"],
        key="tender_upload",
    )

    st.markdown("---")
    st.subheader("Upload Bidder Submissions")
    st.markdown("Upload documents for each bidder. Give each bidder a name and upload their files.")

    num_bidders = st.number_input("Number of bidders", min_value=1, max_value=50, value=3)

    bidder_inputs = []
    for i in range(int(num_bidders)):
        with st.expander(f"Bidder {i+1}", expanded=(i == 0)):
            bname = st.text_input(f"Bidder {i+1} Name", key=f"bname_{i}",
                                   placeholder="e.g., Apex Constructions Pvt Ltd")
            bfiles = st.file_uploader(
                f"Upload documents for Bidder {i+1}",
                type=["pdf", "txt", "docx", "jpg", "jpeg", "png"],
                accept_multiple_files=True,
                key=f"bfiles_{i}",
            )
            bidder_inputs.append({"name": bname, "files": bfiles})

    if st.button("Process All Documents", type="primary", key="process_upload"):
        if not tender_name:
            st.warning("Please enter a tender name.")
            st.stop()
        if not tender_file:
            st.warning("Please upload a tender document.")
            st.stop()

        progress = st.progress(0, text="Starting...")

        # Save and ingest tender
        progress.progress(5, text="Ingesting tender document...")
        tender_dir = os.path.join(UPLOADS_DIR, tender_name.replace("/", "_"))
        os.makedirs(tender_dir, exist_ok=True)
        tender_path = os.path.join(tender_dir, tender_file.name)
        with open(tender_path, "wb") as f:
            f.write(tender_file.getbuffer())

        tender_data = ingest_document(tender_path)
        tender_id = create_tender(
            name=tender_name,
            filename=tender_file.name,
            file_hash=tender_data["file_hash"],
            full_text=tender_data["full_text"],
        )

        save_document(
            bidder_id=None, tender_id=tender_id,
            filename=tender_data["filename"], file_hash=tender_data["file_hash"],
            file_type=tender_data["file_type"], doc_category="tender_document",
            full_text=tender_data["full_text"], page_count=tender_data["page_count"],
            min_ocr_confidence=tender_data["min_ocr_confidence"], is_tender_doc=True,
        )

        # Extract criteria
        progress.progress(20, text="Extracting eligibility criteria from tender (LLM)...")
        criteria = extract_criteria(tender_data["full_text"])
        save_criteria(tender_id, criteria)

        # Process each bidder
        total_bidders = len([b for b in bidder_inputs if b["name"] and b["files"]])
        for idx, bi in enumerate(bidder_inputs):
            if not bi["name"] or not bi["files"]:
                continue

            pct = 30 + int(60 * (idx / max(total_bidders, 1)))
            progress.progress(pct, text=f"Processing bidder: {bi['name']}...")

            bidder_id = create_bidder(tender_id, bi["name"])
            bidder_dir = os.path.join(tender_dir, bi["name"].replace("/", "_").replace(" ", "_"))
            os.makedirs(bidder_dir, exist_ok=True)

            for uploaded_file in bi["files"]:
                fpath = os.path.join(bidder_dir, uploaded_file.name)
                with open(fpath, "wb") as f:
                    f.write(uploaded_file.getbuffer())

                doc_data = ingest_document(fpath)
                save_document(
                    bidder_id=bidder_id, tender_id=tender_id,
                    filename=doc_data["filename"], file_hash=doc_data["file_hash"],
                    file_type=doc_data["file_type"], doc_category="pending_classification",
                    full_text=doc_data["full_text"], page_count=doc_data["page_count"],
                    min_ocr_confidence=doc_data["min_ocr_confidence"],
                )

        progress.progress(100, text="Done!")
        st.success(f"Tender processed! {len(criteria)} criteria extracted. "
                    f"{total_bidders} bidders uploaded.")
        st.info("Go to **Step 2: Review Criteria** in the sidebar.")
        st.session_state["active_tender_id"] = tender_id


# ════════════════════════════════════════
# TAB 2: Use Sample Data
# ════════════════════════════════════════
with tab_sample:
    st.subheader("Load Sample Tender and Bidder Data")
    st.markdown("Use the included sample CRPF construction tender with 10 mock bidders.")

    sample_tender_path = os.path.join(SAMPLE_DATA_DIR, "tender", "CRPF_Tender_2026_001.txt")
    sample_bidders_dir = os.path.join(SAMPLE_DATA_DIR, "bidders")

    if not os.path.exists(sample_tender_path):
        st.warning(
            f"Sample data not found at `{SAMPLE_DATA_DIR}`. "
            "Make sure the sample_data folder is in the tenderlens directory."
        )
        st.stop()

    if st.button("Load Sample Data", type="primary", key="load_sample"):
        progress = st.progress(0, text="Loading sample tender...")

        # Ingest sample tender
        tender_data = ingest_document(sample_tender_path)
        tender_id = create_tender(
            name="CRPF/GC/NDL/CONST/2026-27/001",
            filename="CRPF_Tender_2026_001.txt",
            file_hash=tender_data["file_hash"],
            full_text=tender_data["full_text"],
        )

        save_document(
            bidder_id=None, tender_id=tender_id,
            filename=tender_data["filename"], file_hash=tender_data["file_hash"],
            file_type=tender_data["file_type"], doc_category="tender_document",
            full_text=tender_data["full_text"], page_count=tender_data["page_count"],
            min_ocr_confidence=tender_data["min_ocr_confidence"], is_tender_doc=True,
        )

        # Extract criteria via LLM
        progress.progress(15, text="Extracting criteria from tender (LLM)...")
        criteria = extract_criteria(tender_data["full_text"])
        save_criteria(tender_id, criteria)

        # Load each sample bidder JSON as a simulated submission
        progress.progress(40, text="Loading sample bidders...")
        bidder_files = sorted([
            f for f in os.listdir(sample_bidders_dir) if f.endswith(".json")
        ])

        for idx, bf in enumerate(bidder_files):
            pct = 40 + int(50 * (idx / max(len(bidder_files), 1)))
            bidder_path = os.path.join(sample_bidders_dir, bf)
            with open(bidder_path, "r") as f:
                bidder_data = json.load(f)

            bidder_name = bidder_data.get("company_name", bf.replace(".json", ""))
            progress.progress(pct, text=f"Loading bidder: {bidder_name}...")

            bidder_id = create_bidder(tender_id, bidder_name)

            # Store the JSON content as a document for analysis
            full_text = json.dumps(bidder_data, indent=2)
            save_document(
                bidder_id=bidder_id, tender_id=tender_id,
                filename=bf, file_hash="",
                file_type="json", doc_category="bidder_submission_json",
                full_text=full_text, page_count=1,
                min_ocr_confidence=None,
            )

        progress.progress(100, text="Done!")
        st.success(
            f"Sample data loaded! {len(criteria)} criteria extracted. "
            f"{len(bidder_files)} bidders loaded."
        )
        st.info("Go to **Step 2: Review Criteria** in the sidebar.")
        st.session_state["active_tender_id"] = tender_id


# ── Show existing tenders ──
st.markdown("---")
st.subheader("Existing Tenders")
tenders = get_all_tenders()
if tenders:
    for t in tenders:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"**{t['name']}** — {t['filename']} ({t['created_at']})")
        with col2:
            if st.button("Select", key=f"select_{t['id']}"):
                st.session_state["active_tender_id"] = t["id"]
                st.success(f"Selected tender: {t['name']}")
else:
    st.info("No tenders yet. Upload a tender document above.")
