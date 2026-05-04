import streamlit as st
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    VERDICT_ELIGIBLE, VERDICT_NOT_ELIGIBLE, VERDICT_NEEDS_REVIEW,
    MAX_PARALLEL_LLM, OLLAMA_FAST_MODEL, OLLAMA_MODEL,
    EVIDENCE_USE_RAG_RETRIEVAL,
    RESERVE_EVIDENCE_DOCS,
)
from database.db import (
    init_db, get_criteria, get_bidders, get_documents,
    get_all_tenders, save_evidence, save_verdict,
    get_all_verdicts, log_audit,
    save_override, get_evidence,
    get_tender,
)
from modules.bidder_processor import (
    build_bidder_corpus, retrieve_top_chunks, extract_evidence,
    extract_evidence_sequential_docs,
)
from modules.evaluator import evaluate_criterion, compute_overall_verdict
from modules import llm
from modules.ui_theme import apply_theme, page_kicker_step
from modules.session_workspace import maybe_bind_ephemeral_session

st.set_page_config(page_title="Evaluation | TenderLens", layout="wide")
init_db()
maybe_bind_ephemeral_session()

apply_theme()
page_kicker_step("Step 3 · Eligibility decision")
st.title("Bidder eligibility review")

# ── Select tender ──
tender_id = st.session_state.get("active_tender_id")
tenders = get_all_tenders()

if not tenders:
    st.warning("No tenders found. Start from Step 1.")
    st.stop()

tender_options = {t["id"]: t["name"] for t in tenders}
selected_id = st.selectbox(
    "Select Tender",
    options=list(tender_options.keys()),
    format_func=lambda x: tender_options[x],
    index=0 if tender_id not in tender_options else list(tender_options.keys()).index(tender_id),
)
st.session_state["active_tender_id"] = selected_id

_t = get_tender(selected_id)
if _t:
    st.caption(
        f"**{_t['name']}** — results are stored for this tender. "
        "Use *Back to criteria* if you need to adjust rules; you do not need to re-upload files."
    )

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


# ── Helpers for the parallel evaluation ──

def _evidence_already_extracted(bidder_id: int, criterion_id: int) -> bool:
    """Check if any evidence rows exist for a (bidder, criterion) pair."""
    try:
        rows = get_evidence(bidder_id, criterion_id)
        return len(rows) > 0
    except Exception:
        return False


def _evaluate_one_bidder(bidder: dict, criteria: list, pending_crit_ids: set,
                         progress_state: dict, progress_lock: threading.Lock) -> dict:
    """Process all pending criteria for a single bidder.

    Default: sequential document scan (each file, chunk batches). The same file may
    be scanned for multiple criteria so combined certificates work. Set
    TENDERLENS_RESERVE_EVIDENCE_DOCS=1 to skip files already used for an earlier
    criterion for this bidder. Set TENDERLENS_EVIDENCE_RAG=1 to use Chroma retrieval.
    """
    bid_id = bidder["id"]

    def _set(**kwargs):
        with progress_lock:
            progress_state.setdefault(bid_id, {}).update(kwargs)

    docs = get_documents(bid_id)
    pending_criteria = [c for c in criteria if c["criterion_id"] in pending_crit_ids]
    if not pending_criteria:
        _set(stage="done", current=0, total=0)
        return {"bidder": bidder, "evidence_writes": [], "verdict_writes": [], "errors": []}

    _set(stage="starting", current=0, total=len(pending_criteria),
         label="building corpus...")
    corpus = build_bidder_corpus(docs)

    if not corpus:
        verdict_writes = []
        for crit in pending_criteria:
            verdict_writes.append({
                "bidder_id": bidder["id"],
                "criterion_id": crit["id"],
                "verdict": VERDICT_NEEDS_REVIEW,
                "explanation": "No usable text could be extracted from this bidder's documents.",
                "confidence": 0.0,
            })
        _set(stage="done", current=len(pending_criteria), total=len(pending_criteria),
             label="empty corpus")
        return {"bidder": bidder, "evidence_writes": [], "verdict_writes": verdict_writes, "errors": []}

    evidence_writes = []
    verdict_writes = []
    errors = []
    total = len(pending_criteria)
    used_docs = set()

    for i, crit in enumerate(pending_criteria, start=1):
        cid = crit["criterion_id"]
        _set(stage="extracting", current=i, total=total,
             label=f"evidence for {cid}")

        try:
            if EVIDENCE_USE_RAG_RETRIEVAL:
                chunks = retrieve_top_chunks(
                    corpus, crit,
                    bidder_id=bid_id,
                    docs=docs,
                    on_index_status=lambda msg: _set(label=msg),
                )
                ev_items = extract_evidence(crit, chunks)
            else:
                excl = used_docs if RESERVE_EVIDENCE_DOCS else None
                ev_items, reserved = extract_evidence_sequential_docs(
                    corpus, crit, excluded_docs=excl,
                )
                if RESERVE_EVIDENCE_DOCS:
                    used_docs |= reserved
        except Exception as e:
            ev_items = []
            errors.append(f"{bidder['name']} / {cid}: extract failed: {e}")

        for ev in ev_items:
            evidence_writes.append({
                "bidder_id": bidder["id"],
                "criterion_id": crit["id"],
                "extracted_value": str(ev.get("extracted_value", "")),
                "raw_text": ev.get("source_text", ""),
                "source_document": ev.get("source_document", ""),
                "source_page": None,
                "confidence": ev.get("ocr_confidence"),
                "notes": ev.get("notes", ""),
            })

        _set(stage="evaluating", current=i, total=total,
             label=f"verdict for {cid}")
        try:
            verdict = evaluate_criterion(crit, ev_items)
        except Exception as e:
            verdict = {
                "verdict": VERDICT_NEEDS_REVIEW,
                "explanation": f"Evaluation error: {e}. Manual review required.",
                "confidence": 0.0,
            }
            errors.append(f"{bidder['name']} / {cid}: {e}")

        verdict_writes.append({
            "bidder_id": bidder["id"],
            "criterion_id": crit["id"],
            "verdict": verdict["verdict"],
            "explanation": verdict["explanation"],
            "confidence": verdict.get("confidence"),
        })

    _set(stage="done", current=total, total=total, label="complete")
    return {
        "bidder": bidder,
        "evidence_writes": evidence_writes,
        "verdict_writes": verdict_writes,
        "errors": errors,
    }


# ── Run Evaluation ──
existing_verdicts = get_all_verdicts(selected_id)

evaluated_pairs = set()
for v in existing_verdicts:
    evaluated_pairs.add((v["bidder_name"], v["crit_code"]))

total_pairs = len(bidders) * len(criteria)
evaluated_count = len(evaluated_pairs)
remaining_count = total_pairs - evaluated_count

if remaining_count > 0:
    if evaluated_count == 0:
        st.info(
            f"Ready to assess **{len(bidders)} bidders** against **{len(criteria)} criteria**. "
            "This may take several minutes. You may return later — completed items stay saved."
        )
        btn_label = "Run evaluation"
    else:
        st.info(
            f"Evaluation is in progress (**{remaining_count}** item(s) still pending). "
            "Continue to complete the review."
        )
        btn_label = "Continue evaluation"

    col_back, col_run = st.columns(2)
    with col_back:
        if st.button("← Back to criteria", type="secondary",
                     width="stretch", key="nav_prev_pre"):
            st.switch_page("pages/2_Review_Criteria.py")
    with col_run:
        run_clicked = st.button(btn_label, type="primary", width="stretch")

    if run_clicked:
        try:
            llm.warmup(model=OLLAMA_FAST_MODEL)
            if OLLAMA_MODEL != OLLAMA_FAST_MODEL:
                llm.warmup(model=OLLAMA_MODEL)
        except Exception:
            pass

        progress = st.progress(0, text="Starting…")
        live_status = st.empty()
        live_table = st.empty()

        bidder_pending_crits = {}
        for bidder in bidders:
            pending = set()
            for c in criteria:
                if (bidder["name"], c["criterion_id"]) in evaluated_pairs:
                    continue
                pending.add(c["criterion_id"])
            bidder_pending_crits[bidder["id"]] = pending

        bidders_to_run = [b for b in bidders if bidder_pending_crits[b["id"]]]

        total_to_run = len(bidders_to_run)
        all_errors = []
        start_time = time.time()

        progress_state = {b["id"]: {"stage": "queued", "current": 0, "total": 0, "label": ""}
                          for b in bidders_to_run}
        progress_lock = threading.Lock()

        STAGE_ICON = {
            "queued": "⏳",
            "starting": "◆",
            "extracting": "◆",
            "evaluating": "◆",
            "done": "✓",
            "error": "⚠",
        }

        FRIENDLY = {
            "queued": "Waiting to start",
            "starting": "Preparing",
            "extracting": "Reviewing documents",
            "evaluating": "Scoring criteria",
            "done": "Complete",
            "error": "Needs attention",
        }

        def _snapshot_state():
            with progress_lock:
                return {bid: dict(s) for bid, s in progress_state.items()}

        def _render_progress(snapshot, processed_results):
            total_units = sum(s.get("total", 0) for s in snapshot.values()) or 1
            done_units = sum(s.get("current", 0) for s in snapshot.values())
            pct = int(100 * done_units / total_units)
            elapsed_now = time.time() - start_time
            progress.progress(
                min(pct, 99),
                text=f"{done_units} of {total_units} steps · {elapsed_now:.0f}s",
            )

            lines = []
            for bidder in bidders_to_run:
                state = snapshot.get(bidder["id"], {})
                stage = state.get("stage", "queued")
                cur = state.get("current", 0)
                tot = state.get("total", 0)
                icon = STAGE_ICON.get(stage, "·")

                if stage == "queued":
                    suffix = FRIENDLY["queued"]
                elif stage == "done":
                    suffix = FRIENDLY["done"]
                elif stage == "error":
                    suffix = FRIENDLY["error"]
                elif tot:
                    suffix = f"{FRIENDLY.get(stage, 'Working')} ({cur}/{tot})"
                else:
                    suffix = FRIENDLY.get(stage, "Working")

                lines.append(f"{icon} **{bidder['name']}** — {suffix}")
            live_status.markdown("\n\n".join(lines))

            if processed_results:
                live = get_all_verdicts(selected_id)
                if live:
                    bidder_names = list(dict.fromkeys(v["bidder_name"] for v in live))
                    rows = []
                    for bn in bidder_names:
                        bv = [v for v in live if v["bidder_name"] == bn]
                        ov = compute_overall_verdict(bv)
                        rows.append({
                            "Bidder": bn,
                            "Eligible criteria": sum(1 for v in bv if v["verdict"] == VERDICT_ELIGIBLE),
                            "Needs review": sum(1 for v in bv if v["verdict"] == VERDICT_NEEDS_REVIEW),
                            "Not eligible": sum(1 for v in bv if v["verdict"] == VERDICT_NOT_ELIGIBLE),
                            "Overall": ov.replace("_", " ").title(),
                        })
                    live_table.dataframe(
                        pd.DataFrame(rows),
                        width="stretch",
                        hide_index=True,
                    )

        _render_progress(_snapshot_state(), processed_results=False)

        max_workers = max(1, min(MAX_PARALLEL_LLM, total_to_run)) if total_to_run else 1

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_bidder = {}
            for bidder in bidders_to_run:
                pending_crit_ids = bidder_pending_crits[bidder["id"]]
                fut = pool.submit(
                    _evaluate_one_bidder, bidder, criteria, pending_crit_ids,
                    progress_state, progress_lock,
                )
                future_to_bidder[fut] = bidder

            pending_futures = set(future_to_bidder.keys())
            any_done = False

            while pending_futures:
                done_now = [f for f in pending_futures if f.done()]
                for f in done_now:
                    pending_futures.discard(f)
                    bidder = future_to_bidder[f]
                    try:
                        result = f.result()
                    except Exception as e:
                        with progress_lock:
                            progress_state[bidder["id"]]["stage"] = "error"
                            progress_state[bidder["id"]]["label"] = str(e)
                        all_errors.append(f"{bidder['name']}: {e}")
                        continue

                    for ev_w in result["evidence_writes"]:
                        try:
                            save_evidence(**ev_w)
                        except Exception as e:
                            all_errors.append(f"DB save_evidence: {e}")
                    for vd_w in result["verdict_writes"]:
                        try:
                            save_verdict(**vd_w)
                        except Exception as e:
                            all_errors.append(f"DB save_verdict: {e}")

                    log_audit(selected_id, "bidder_evaluated", f"Bidder: {bidder['name']}")
                    if result["errors"]:
                        all_errors.extend(result["errors"])
                    any_done = True

                _render_progress(_snapshot_state(), processed_results=any_done)

                if pending_futures:
                    time.sleep(0.5)

            _render_progress(_snapshot_state(), processed_results=True)

        elapsed = time.time() - start_time
        progress.progress(100, text=f"Finished · {elapsed:.0f}s")
        st.success("Evaluation updated. Review the summary below.")
        if all_errors:
            with st.expander(f"{len(all_errors)} non-fatal warnings during evaluation"):
                for msg in all_errors:
                    st.text(msg)
        st.rerun()

# ── Display Results ──
all_verdicts = get_all_verdicts(selected_id)

if all_verdicts:
    st.markdown("---")
    st.subheader("Summary matrix")

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
    st.dataframe(df, width="stretch", hide_index=True)

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

    st.markdown("---")
    st.subheader("Findings by bidder")

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

    st.markdown("---")
    col_prev, col_next = st.columns(2)
    with col_prev:
        if st.button("← Back to criteria", type="secondary",
                     width="stretch", key="nav_prev"):
            st.switch_page("pages/2_Review_Criteria.py")
    with col_next:
        if st.button("Next: Report →", type="primary",
                     width="stretch", key="nav_next"):
            st.switch_page("pages/4_Report.py")
