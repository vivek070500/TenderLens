import os
import sqlite3
import threading
from contextlib import contextmanager

from config import DB_PATH

# Streamlit reruns / callbacks can hit SQLite concurrently; serialize access + wait on locks.
_db_lock = threading.RLock()
_CONNECT_TIMEOUT_S = 30.0
_BUSY_TIMEOUT_MS = 30000


def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def db_connect():
    """Single serialized connection: commit on success, rollback on error, always close."""
    _ensure_dir()
    _db_lock.acquire()
    conn = sqlite3.connect(
        DB_PATH,
        timeout=_CONNECT_TIMEOUT_S,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=%d" % int(_BUSY_TIMEOUT_MS))
    except sqlite3.Error:
        conn.close()
        _db_lock.release()
        raise
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        _db_lock.release()


def init_db():
    """Create all tables from schema.sql."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = f.read()
    with db_connect() as conn:
        conn.executescript(schema)


def log_audit(tender_id: int, action: str, details: str = ""):
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (tender_id, action, details) VALUES (?, ?, ?)",
            (tender_id, action, details),
        )


# ── Tenders ──


def create_tender(name: str, filename: str, file_hash: str, full_text: str) -> int:
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT INTO tenders (name, filename, file_hash, full_text) VALUES (?, ?, ?, ?)",
            (name, filename, file_hash, full_text),
        )
        tender_id = cur.lastrowid
    log_audit(tender_id, "tender_uploaded", f"File: {filename}")
    return tender_id


def get_tender(tender_id: int) -> dict:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM tenders WHERE id = ?", (tender_id,)).fetchone()
    return dict(row) if row else None


def get_all_tenders() -> list:
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM tenders ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def wipe_all_workspaces():
    """Delete every tender and related rows (used for ephemeral / single-session mode)."""
    ids = [t["id"] for t in get_all_tenders()]
    for tid in ids:
        delete_tender(tid)


def delete_tender(tender_id: int):
    """Delete a tender and all related rows (criteria, bidders, documents, evidence, verdicts, overrides, audit_log)."""
    bidder_ids = []
    verdict_ids = []
    with db_connect() as conn:
        bidder_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM bidders WHERE tender_id = ?", (tender_id,)
            ).fetchall()
        ]
        for bid in bidder_ids:
            verdict_ids.extend(
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM verdicts WHERE bidder_id = ?", (bid,)
                ).fetchall()
            )

        if verdict_ids:
            placeholders = ",".join("?" * len(verdict_ids))
            conn.execute(
                f"DELETE FROM officer_overrides WHERE verdict_id IN ({placeholders})",
                verdict_ids,
            )
        if bidder_ids:
            try:
                from modules import rag_index as _rag

                for _bid in bidder_ids:
                    _rag.delete_bidder_index(_bid)
            except Exception:
                pass
            placeholders = ",".join("?" * len(bidder_ids))
            conn.execute(f"DELETE FROM evidence WHERE bidder_id IN ({placeholders})", bidder_ids)
            conn.execute(f"DELETE FROM verdicts WHERE bidder_id IN ({placeholders})", bidder_ids)
            conn.execute(f"DELETE FROM documents WHERE bidder_id IN ({placeholders})", bidder_ids)

        conn.execute("DELETE FROM documents WHERE tender_id = ?", (tender_id,))
        conn.execute("DELETE FROM criteria WHERE tender_id = ?", (tender_id,))
        conn.execute("DELETE FROM bidders WHERE tender_id = ?", (tender_id,))
        conn.execute("DELETE FROM audit_log WHERE tender_id = ?", (tender_id,))
        conn.execute("DELETE FROM tenders WHERE id = ?", (tender_id,))


# ── Criteria ──


def save_criteria(tender_id: int, criteria_list: list):
    with db_connect() as conn:
        existing = {}
        for row in conn.execute(
            "SELECT id, criterion_id FROM criteria WHERE tender_id = ?", (tender_id,)
        ).fetchall():
            existing[row["criterion_id"]] = row["id"]

        seen_ids = set()
        for c in criteria_list:
            crit_code = c.get("criterion_id", "")
            values = (
                c.get("description", ""),
                c.get("category", ""),
                1 if c.get("mandatory", True) else 0,
                c.get("threshold", ""),
                c.get("expected_evidence", ""),
                c.get("source_section", ""),
            )
            if crit_code in existing:
                conn.execute(
                    """UPDATE criteria
                       SET description=?, category=?, mandatory=?, threshold=?,
                           expected_evidence=?, source_section=?
                       WHERE id=?""",
                    values + (existing[crit_code],),
                )
                seen_ids.add(crit_code)
            else:
                conn.execute(
                    """INSERT INTO criteria
                       (tender_id, criterion_id, description, category, mandatory,
                        threshold, expected_evidence, source_section)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (tender_id, crit_code) + values,
                )
                seen_ids.add(crit_code)

        for crit_code, row_id in existing.items():
            if crit_code not in seen_ids:
                ev_count = conn.execute(
                    "SELECT COUNT(*) FROM evidence WHERE criterion_id = ?", (row_id,)
                ).fetchone()[0]
                vd_count = conn.execute(
                    "SELECT COUNT(*) FROM verdicts WHERE criterion_id = ?", (row_id,)
                ).fetchone()[0]
                if ev_count == 0 and vd_count == 0:
                    conn.execute("DELETE FROM criteria WHERE id = ?", (row_id,))
    log_audit(tender_id, "criteria_saved", f"{len(criteria_list)} criteria")


def replace_criteria_for_new_upload(tender_id: int, criteria_list: list):
    """Delete all criteria for a tender and insert a fresh extract.

    Used immediately after intake (Step 1) when no evidence/verdicts exist yet,
    so stale rows from earlier DB state or prior extracts cannot remain.
    """
    with db_connect() as conn:
        conn.execute("DELETE FROM criteria WHERE tender_id = ?", (tender_id,))
        for c in criteria_list:
            crit_code = c.get("criterion_id", "")
            values = (
                c.get("description", ""),
                c.get("category", ""),
                1 if c.get("mandatory", True) else 0,
                c.get("threshold", ""),
                c.get("expected_evidence", ""),
                c.get("source_section", ""),
            )
            conn.execute(
                """INSERT INTO criteria
                   (tender_id, criterion_id, description, category, mandatory,
                    threshold, expected_evidence, source_section)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (tender_id, crit_code) + values,
            )
    log_audit(
        tender_id,
        "criteria_saved",
        f"{len(criteria_list)} criteria (fresh extract)",
    )


def get_criteria(tender_id: int) -> list:
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM criteria WHERE tender_id = ? ORDER BY id", (tender_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def confirm_criteria(tender_id: int):
    with db_connect() as conn:
        conn.execute("UPDATE criteria SET confirmed = 1 WHERE tender_id = ?", (tender_id,))
    log_audit(tender_id, "criteria_confirmed", "Officer confirmed criteria list")


# ── Bidders ──


def create_bidder(tender_id: int, name: str) -> int:
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT INTO bidders (tender_id, name) VALUES (?, ?)", (tender_id, name)
        )
        bidder_id = cur.lastrowid
    log_audit(tender_id, "bidder_added", f"Bidder: {name}")
    return bidder_id


def get_bidders(tender_id: int) -> list:
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bidders WHERE tender_id = ? ORDER BY id", (tender_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Documents ──


def save_document(
    bidder_id: int,
    tender_id: int,
    filename: str,
    file_hash: str,
    file_type: str,
    doc_category: str,
    full_text: str,
    page_count: int,
    min_ocr_confidence: float,
    is_tender_doc: bool = False,
) -> int:
    with db_connect() as conn:
        cur = conn.execute(
            """INSERT INTO documents
               (bidder_id, tender_id, filename, file_hash, file_type, doc_category,
                full_text, page_count, min_ocr_confidence, is_tender_doc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bidder_id,
                tender_id,
                filename,
                file_hash,
                file_type,
                doc_category,
                full_text,
                page_count,
                min_ocr_confidence,
                1 if is_tender_doc else 0,
            ),
        )
        return cur.lastrowid


def get_documents(bidder_id: int) -> list:
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE bidder_id = ? ORDER BY id", (bidder_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Evidence ──


def save_evidence(
    bidder_id: int,
    criterion_id: int,
    extracted_value: str,
    raw_text: str,
    source_document: str,
    source_page: int,
    confidence: float,
    notes: str = "",
) -> int:
    with db_connect() as conn:
        cur = conn.execute(
            """INSERT INTO evidence
               (bidder_id, criterion_id, extracted_value, raw_text,
                source_document, source_page, confidence, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bidder_id,
                criterion_id,
                extracted_value,
                raw_text,
                source_document,
                source_page,
                confidence,
                notes,
            ),
        )
        return cur.lastrowid


def get_evidence(bidder_id: int, criterion_id: int = None) -> list:
    with db_connect() as conn:
        if criterion_id:
            rows = conn.execute(
                "SELECT * FROM evidence WHERE bidder_id = ? AND criterion_id = ?",
                (bidder_id, criterion_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM evidence WHERE bidder_id = ?", (bidder_id,)
            ).fetchall()
    return [dict(r) for r in rows]


# ── Verdicts ──


def save_verdict(
    bidder_id: int,
    criterion_id: int,
    verdict: str,
    explanation: str,
    confidence: float = None,
) -> int:
    with db_connect() as conn:
        existing = conn.execute(
            "SELECT id FROM verdicts WHERE bidder_id = ? AND criterion_id = ?",
            (bidder_id, criterion_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE verdicts SET verdict=?, explanation=?, confidence=? WHERE id=?",
                (verdict, explanation, confidence, existing["id"]),
            )
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO verdicts (bidder_id, criterion_id, verdict, explanation, confidence)
               VALUES (?, ?, ?, ?, ?)""",
            (bidder_id, criterion_id, verdict, explanation, confidence),
        )
        return cur.lastrowid


def get_verdicts(bidder_id: int) -> list:
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT v.*, c.criterion_id as crit_code, c.description as crit_desc, c.category
               FROM verdicts v
               JOIN criteria c ON v.criterion_id = c.id
               WHERE v.bidder_id = ?
               ORDER BY c.id""",
            (bidder_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_verdicts(tender_id: int) -> list:
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT v.*, b.name as bidder_name,
                      c.criterion_id as crit_code, c.description as crit_desc, c.category
               FROM verdicts v
               JOIN bidders b ON v.bidder_id = b.id
               JOIN criteria c ON v.criterion_id = c.id
               WHERE b.tender_id = ?
               ORDER BY b.id, c.id""",
            (tender_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Officer Overrides ──


def save_override(
    verdict_id: int,
    original_verdict: str,
    new_verdict: str,
    reason: str,
    officer_name: str = "",
):
    with db_connect() as conn:
        conn.execute(
            """INSERT INTO officer_overrides
               (verdict_id, original_verdict, new_verdict, reason, officer_name)
               VALUES (?, ?, ?, ?, ?)""",
            (verdict_id, original_verdict, new_verdict, reason, officer_name),
        )
        conn.execute(
            "UPDATE verdicts SET verdict = ? WHERE id = ?", (new_verdict, verdict_id)
        )


def get_overrides(tender_id: int) -> list:
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT o.*, v.bidder_id, b.name as bidder_name,
                      c.criterion_id as crit_code, c.description as crit_desc
               FROM officer_overrides o
               JOIN verdicts v ON o.verdict_id = v.id
               JOIN bidders b ON v.bidder_id = b.id
               JOIN criteria c ON v.criterion_id = c.id
               WHERE b.tender_id = ?
               ORDER BY o.created_at""",
            (tender_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Audit Log ──


def get_audit_log(tender_id: int) -> list:
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE tender_id = ? ORDER BY created_at",
            (tender_id,),
        ).fetchall()
    return [dict(r) for r in rows]
