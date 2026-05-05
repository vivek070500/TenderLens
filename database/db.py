import sqlite3
import os
import json
from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables from schema.sql."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        schema = f.read()
    conn = get_connection()
    conn.executescript(schema)
    conn.commit()
    conn.close()


def log_audit(tender_id: int, action: str, details: str = ""):
    conn = get_connection()
    conn.execute(
        "INSERT INTO audit_log (tender_id, action, details) VALUES (?, ?, ?)",
        (tender_id, action, details),
    )
    conn.commit()
    conn.close()


# ── Tenders ──

def create_tender(name: str, filename: str, file_hash: str, full_text: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO tenders (name, filename, file_hash, full_text) VALUES (?, ?, ?, ?)",
        (name, filename, file_hash, full_text),
    )
    tender_id = cur.lastrowid
    conn.commit()
    conn.close()
    log_audit(tender_id, "tender_uploaded", f"File: {filename}")
    return tender_id


def get_tender(tender_id: int) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM tenders WHERE id = ?", (tender_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_tenders() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM tenders ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def wipe_all_workspaces():
    """Delete every tender and related rows (used for ephemeral / single-session mode)."""
    ids = [t["id"] for t in get_all_tenders()]
    for tid in ids:
        delete_tender(tid)


def delete_tender(tender_id: int):
    """Delete a tender and all related rows (criteria, bidders, documents, evidence, verdicts, overrides, audit_log)."""
    conn = get_connection()
    bidder_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM bidders WHERE tender_id = ?", (tender_id,)
        ).fetchall()
    ]
    verdict_ids = []
    for bid in bidder_ids:
        verdict_ids.extend(
            r["id"] for r in conn.execute(
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

    conn.commit()
    conn.close()


# ── Criteria ──

def save_criteria(tender_id: int, criteria_list: list):
    conn = get_connection()
    # Get existing criteria keyed by criterion_id (e.g. "C-001")
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
            # Update existing row (preserves the primary key for FK references)
            conn.execute(
                """UPDATE criteria
                   SET description=?, category=?, mandatory=?, threshold=?,
                       expected_evidence=?, source_section=?
                   WHERE id=?""",
                values + (existing[crit_code],),
            )
            seen_ids.add(crit_code)
        else:
            # Insert new criterion
            conn.execute(
                """INSERT INTO criteria
                   (tender_id, criterion_id, description, category, mandatory,
                    threshold, expected_evidence, source_section)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (tender_id, crit_code) + values,
            )

    # Only delete criteria that were removed AND have no FK references
    for crit_code, row_id in existing.items():
        if crit_code not in seen_ids:
            # Check if any evidence or verdicts reference this criterion
            ev_count = conn.execute(
                "SELECT COUNT(*) FROM evidence WHERE criterion_id = ?", (row_id,)
            ).fetchone()[0]
            vd_count = conn.execute(
                "SELECT COUNT(*) FROM verdicts WHERE criterion_id = ?", (row_id,)
            ).fetchone()[0]
            if ev_count == 0 and vd_count == 0:
                conn.execute("DELETE FROM criteria WHERE id = ?", (row_id,))

    conn.commit()
    conn.close()
    log_audit(tender_id, "criteria_saved", f"{len(criteria_list)} criteria")


def replace_criteria_for_new_upload(tender_id: int, criteria_list: list):
    """Delete all criteria for a tender and insert a fresh extract from Step 1.

    Cleans up evidence / verdicts that reference removed criteria rows so
    FK constraints succeed when callers re-run extraction after evaluations.
    """
    conn = get_connection()
    crit_rows = conn.execute(
        "SELECT id FROM criteria WHERE tender_id = ?", (tender_id,)
    ).fetchall()
    crit_ids = [r["id"] for r in crit_rows]
    if crit_ids:
        ph = ",".join("?" * len(crit_ids))
        verdict_ids = [
            r["id"]
            for r in conn.execute(
                f"SELECT id FROM verdicts WHERE criterion_id IN ({ph})", crit_ids
            ).fetchall()
        ]
        if verdict_ids:
            ph_v = ",".join("?" * len(verdict_ids))
            conn.execute(
                f"DELETE FROM officer_overrides WHERE verdict_id IN ({ph_v})",
                verdict_ids,
            )
        conn.execute(f"DELETE FROM verdicts WHERE criterion_id IN ({ph})", crit_ids)
        conn.execute(f"DELETE FROM evidence WHERE criterion_id IN ({ph})", crit_ids)

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

    conn.commit()
    conn.close()
    log_audit(
        tender_id,
        "criteria_saved",
        f"{len(criteria_list)} criteria (fresh extract)",
    )


def get_criteria(tender_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM criteria WHERE tender_id = ? ORDER BY id", (tender_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def confirm_criteria(tender_id: int):
    conn = get_connection()
    conn.execute("UPDATE criteria SET confirmed = 1 WHERE tender_id = ?", (tender_id,))
    conn.commit()
    conn.close()
    log_audit(tender_id, "criteria_confirmed", "Officer confirmed criteria list")


# ── Bidders ──

def create_bidder(tender_id: int, name: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO bidders (tender_id, name) VALUES (?, ?)", (tender_id, name)
    )
    bidder_id = cur.lastrowid
    conn.commit()
    conn.close()
    log_audit(tender_id, "bidder_added", f"Bidder: {name}")
    return bidder_id


def get_bidders(tender_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bidders WHERE tender_id = ? ORDER BY id", (tender_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Documents ──

def save_document(bidder_id: int, tender_id: int, filename: str, file_hash: str,
                   file_type: str, doc_category: str, full_text: str,
                   page_count: int, min_ocr_confidence: float,
                   is_tender_doc: bool = False) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO documents
           (bidder_id, tender_id, filename, file_hash, file_type, doc_category,
            full_text, page_count, min_ocr_confidence, is_tender_doc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (bidder_id, tender_id, filename, file_hash, file_type, doc_category,
         full_text, page_count, min_ocr_confidence, 1 if is_tender_doc else 0),
    )
    doc_id = cur.lastrowid
    conn.commit()
    conn.close()
    return doc_id


def get_documents(bidder_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM documents WHERE bidder_id = ? ORDER BY id", (bidder_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Evidence ──

def save_evidence(bidder_id: int, criterion_id: int, extracted_value: str,
                  raw_text: str, source_document: str, source_page: int,
                  confidence: float, notes: str = "") -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO evidence
           (bidder_id, criterion_id, extracted_value, raw_text,
            source_document, source_page, confidence, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (bidder_id, criterion_id, extracted_value, raw_text,
         source_document, source_page, confidence, notes),
    )
    eid = cur.lastrowid
    conn.commit()
    conn.close()
    return eid


def get_evidence(bidder_id: int, criterion_id: int = None) -> list:
    conn = get_connection()
    if criterion_id:
        rows = conn.execute(
            "SELECT * FROM evidence WHERE bidder_id = ? AND criterion_id = ?",
            (bidder_id, criterion_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM evidence WHERE bidder_id = ?", (bidder_id,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Verdicts ──

def save_verdict(bidder_id: int, criterion_id: int, verdict: str,
                 explanation: str, confidence: float = None) -> int:
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM verdicts WHERE bidder_id = ? AND criterion_id = ?",
        (bidder_id, criterion_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE verdicts SET verdict=?, explanation=?, confidence=? WHERE id=?",
            (verdict, explanation, confidence, existing["id"]),
        )
        vid = existing["id"]
    else:
        cur = conn.execute(
            """INSERT INTO verdicts (bidder_id, criterion_id, verdict, explanation, confidence)
               VALUES (?, ?, ?, ?, ?)""",
            (bidder_id, criterion_id, verdict, explanation, confidence),
        )
        vid = cur.lastrowid
    conn.commit()
    conn.close()
    return vid


def get_verdicts(bidder_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        """SELECT v.*, c.criterion_id as crit_code, c.description as crit_desc, c.category
           FROM verdicts v
           JOIN criteria c ON v.criterion_id = c.id
           WHERE v.bidder_id = ?
           ORDER BY c.id""",
        (bidder_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_verdicts(tender_id: int) -> list:
    conn = get_connection()
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
    conn.close()
    return [dict(r) for r in rows]


# ── Officer Overrides ──

def save_override(verdict_id: int, original_verdict: str, new_verdict: str,
                  reason: str, officer_name: str = ""):
    conn = get_connection()
    conn.execute(
        """INSERT INTO officer_overrides
           (verdict_id, original_verdict, new_verdict, reason, officer_name)
           VALUES (?, ?, ?, ?, ?)""",
        (verdict_id, original_verdict, new_verdict, reason, officer_name),
    )
    conn.execute(
        "UPDATE verdicts SET verdict = ? WHERE id = ?", (new_verdict, verdict_id)
    )
    conn.commit()
    conn.close()


def get_overrides(tender_id: int) -> list:
    conn = get_connection()
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
    conn.close()
    return [dict(r) for r in rows]


# ── Audit Log ──

def get_audit_log(tender_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE tender_id = ? ORDER BY created_at",
        (tender_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
