import streamlit as st
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import UPLOADS_DIR, PERSIST_WORKSPACE
from database.db import (
    init_db, create_tender, create_bidder, save_document,
    replace_criteria_for_new_upload, get_all_tenders, delete_tender,
    get_tender, get_criteria, get_bidders,
)
from modules.ingestion import ingest_document
from modules.tender_analyzer import extract_criteria
from modules import llm
from modules.ui_theme import apply_theme, page_kicker_step, render_session_resume_card
from modules.session_workspace import maybe_bind_ephemeral_session, wipe_before_new_registration

st.set_page_config(page_title="Upload Documents | TenderLens", layout="wide")
init_db()
maybe_bind_ephemeral_session()

apply_theme()
page_kicker_step("Step 1 · Document intake")
st.title("Upload tender and bidder documents")

if not llm.is_available():
    st.error(
        "The analysis service is not running. Start Ollama, then refresh this page."
    )
    st.stop()

st.caption("Connected to the local language model service.")

active_id = st.session_state.get("active_tender_id")
active_tender = get_tender(active_id) if active_id else None

if active_tender:
    _crits = get_criteria(active_id)
    _bidders = get_bidders(active_id)
    render_session_resume_card(
        active_tender["name"],
        active_tender.get("filename") or "—",
        criteria_count=len(_crits),
        criteria_confirmed=bool(_crits) and all(c["confirmed"] for c in _crits),
        bidders_count=len(_bidders),
    )

tenders = get_all_tenders()
if PERSIST_WORKSPACE and len(tenders) > 1:
    ids = [t["id"] for t in tenders]
    label_by_id = {t["id"]: t["name"] for t in tenders}
    prev = st.session_state.get("active_tender_id")
    ix = ids.index(prev) if prev in ids else 0
    picked = st.selectbox(
        "Active tender",
        options=ids,
        index=ix,
        format_func=lambda i: f"{label_by_id[i]} (#{i})",
        key="intake_active_tender_pick",
    )
    if picked != prev:
        st.session_state["active_tender_id"] = picked
        st.session_state["upload_complete"] = True
        st.rerun()

start_expanded = active_tender is None and len(tenders) <= 1
with st.expander(
    "Register a new procurement (upload tender and bidder documents)",
    expanded=start_expanded,
):
    if PERSIST_WORKSPACE:
        st.caption(
            "Use when starting another procurement. Use the **current session** card above "
            "to continue the open tender without re-uploading."
        )
    else:
        st.caption(
            "Submits a new procurement and **replaces** any work already loaded in this tab. "
            "Use the **current session** card above to continue without re-uploading."
        )
    tender_name = st.text_input(
        "Tender name / reference",
        placeholder="e.g. CRPF/CONST/2026-27/001",
        key="new_tender_name",
    )
    tender_file = st.file_uploader(
        "Tender document",
        type=["pdf", "txt", "docx", "jpg", "jpeg", "png"],
        help="PDF, Word, text, or a photo/scan (OCR).",
        key="tender_upload",
    )

    st.markdown("**Bidders**")
    num_bidders = st.number_input("Number of bidders", min_value=1, max_value=50, value=3)

    bidder_inputs = []
    for i in range(int(num_bidders)):
        with st.expander(f"Bidder {i + 1}", expanded=(i == 0)):
            bname = st.text_input(
                f"Bidder {i + 1} name",
                key=f"bname_{i}",
                placeholder="e.g. Apex Constructions Pvt Ltd",
            )
            bfiles = st.file_uploader(
                f"Documents for bidder {i + 1}",
                type=["pdf", "txt", "docx", "jpg", "jpeg", "png"],
                accept_multiple_files=True,
                key=f"bfiles_{i}",
            )
            bidder_inputs.append({"name": bname, "files": bfiles})

    if st.button("Process and extract criteria", type="primary", key="process_upload"):
        if not tender_name:
            st.warning("Enter a tender name.")
            st.stop()
        if not tender_file:
            st.warning("Upload the tender document.")
            st.stop()

        wipe_before_new_registration()

        progress = st.progress(0, text="Preparing…")

        progress.progress(5, text="Reading tender document…")
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

        progress.progress(20, text="Extracting eligibility criteria…")
        criteria = extract_criteria(tender_data["full_text"])
        replace_criteria_for_new_upload(tender_id, criteria)

        total_bidders = len([b for b in bidder_inputs if b["name"] and b["files"]])
        for idx, bi in enumerate(bidder_inputs):
            if not bi["name"] or not bi["files"]:
                continue

            pct = 30 + int(60 * (idx / max(total_bidders, 1)))
            progress.progress(pct, text=f"Processing bidder: {bi['name']}…")

            bidder_id = create_bidder(tender_id, bi["name"])
            bidder_dir = os.path.join(
                tender_dir, bi["name"].replace("/", "_").replace(" ", "_")
            )
            os.makedirs(bidder_dir, exist_ok=True)

            for uploaded_file in bi["files"]:
                fpath = os.path.join(bidder_dir, uploaded_file.name)
                with open(fpath, "wb") as f:
                    f.write(uploaded_file.getbuffer())

                doc_data = ingest_document(fpath)
                save_document(
                    bidder_id=bidder_id, tender_id=tender_id,
                    filename=doc_data["filename"], file_hash=doc_data["file_hash"],
                    file_type=doc_data["file_type"], doc_category="bidder_document",
                    full_text=doc_data["full_text"], page_count=doc_data["page_count"],
                    min_ocr_confidence=doc_data["min_ocr_confidence"],
                )

        progress.progress(100, text="Complete")
        st.success(
            f"Registration complete · {len(criteria)} criteria · {total_bidders} bidders"
        )
        st.session_state["active_tender_id"] = tender_id
        st.session_state["upload_complete"] = True
        st.rerun()

if PERSIST_WORKSPACE:
    with st.expander("Remove a stored tender", expanded=False):
        st.caption("Deletes one saved procurement and its evaluations from this computer.")
        if not tenders:
            st.caption("No tenders stored.")
        else:
            del_opts = {t["id"]: t["name"] for t in tenders}
            del_id = st.selectbox(
                "Tender to remove",
                options=list(del_opts.keys()),
                format_func=lambda x: del_opts[x],
                key="delete_tender_pick",
            )
            if st.button("Delete this tender permanently", type="secondary", key="confirm_delete_tender"):
                delete_tender(del_id)
                if st.session_state.get("active_tender_id") == del_id:
                    st.session_state.pop("active_tender_id", None)
                    st.session_state.pop("upload_complete", None)
                st.rerun()
