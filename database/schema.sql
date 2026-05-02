CREATE TABLE IF NOT EXISTS tenders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    filename TEXT,
    file_hash TEXT,
    full_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS criteria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL,
    criterion_id TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    mandatory INTEGER NOT NULL DEFAULT 1,
    threshold TEXT,
    expected_evidence TEXT,
    source_section TEXT,
    confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tender_id) REFERENCES tenders(id)
);

CREATE TABLE IF NOT EXISTS bidders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tender_id) REFERENCES tenders(id)
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bidder_id INTEGER,
    tender_id INTEGER,
    filename TEXT NOT NULL,
    file_hash TEXT,
    file_type TEXT,
    doc_category TEXT,
    full_text TEXT,
    page_count INTEGER,
    min_ocr_confidence REAL,
    is_tender_doc INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bidder_id) REFERENCES bidders(id),
    FOREIGN KEY (tender_id) REFERENCES tenders(id)
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bidder_id INTEGER NOT NULL,
    criterion_id INTEGER NOT NULL,
    extracted_value TEXT,
    raw_text TEXT,
    source_document TEXT,
    source_page INTEGER,
    confidence REAL,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bidder_id) REFERENCES bidders(id),
    FOREIGN KEY (criterion_id) REFERENCES criteria(id)
);

CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bidder_id INTEGER NOT NULL,
    criterion_id INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    explanation TEXT,
    confidence REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bidder_id) REFERENCES bidders(id),
    FOREIGN KEY (criterion_id) REFERENCES criteria(id)
);

CREATE TABLE IF NOT EXISTS officer_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verdict_id INTEGER NOT NULL,
    original_verdict TEXT NOT NULL,
    new_verdict TEXT NOT NULL,
    reason TEXT NOT NULL,
    officer_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (verdict_id) REFERENCES verdicts(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER,
    action TEXT NOT NULL,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tender_id) REFERENCES tenders(id)
);
