"""Bidder processing — document chunks, retrieval, and evidence extraction.

Two ways to feed *extract_evidence*:

1. **Sequential document scan** (default for evaluation): for each document, try
   consecutive chunk batches until evidence is found or the file is exhausted.
   Documents are ordered by keyword overlap with the criterion (likely files first),
   with a full fallback order when scores tie at zero.

2. **Top-K retrieval** (optional): vector search (Chroma + embeddings) and/or
   global keyword chunk ranking — useful when ``TENDERLENS_EVIDENCE_RAG=1``.
"""
import os
import re
from modules import llm
from modules import chunking
from modules.money_pipeline import comma_grouped_indian_or_western, parse_money
from modules.ingestion import ingest_document
from config import DOC_SCAN_BATCH_SIZE, PROMPTS_DIR, RESERVE_EVIDENCE_DOCS


def _load_prompt(name: str) -> str:
    path = os.path.join(PROMPTS_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Currency / money (hybrid rules + optional LLM — see money_pipeline.py)
# ─────────────────────────────────────────────────────────────────────────────

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "twenty-five": 25, "fifty": 50, "hundred": 100,
}


def normalize_indian_currency(value_str) -> float:
    """Parse Indian currency strings into a numeric value (rupees, major units)."""
    if not value_str:
        return None
    r = parse_money(str(value_str), default_currency="INR", use_llm=False)
    if r and r.currency == "INR":
        return float(r.amount)
    return None


def extract_currency_from_text(text) -> float:
    """Primary INR amount in a fragment (rules + optional LLM via env)."""
    if not text:
        return None
    r = parse_money(str(text), default_currency="INR", prefer_currency="INR", use_llm=None)
    if not r:
        return None
    if r.currency == "INR":
        return float(r.amount)
    return None


def currency_amount_from_evidence(ev: dict):
    """INR amount from an evidence row (rules only — avoids N× LLM per evaluation)."""
    if not ev:
        return None
    blob = f"{ev.get('extracted_value') or ''} {ev.get('source_text') or ''}"
    r = parse_money(blob, default_currency="INR", prefer_currency="INR", use_llm=False)
    if r and r.currency == "INR":
        return float(r.amount)
    return None


def parse_count_requirement(text) -> int:
    """Detect 'at least N' / 'three (3) similar works' patterns."""
    if not text:
        return 1
    t = str(text).lower()

    patterns = [
        r"at\s+least\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)",
        r"minimum\s+(?:of\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)",
        r"\b(?:three|two|four|five|six|seven|eight|nine|ten)\s*\((\d+)\)",
        r"\((\d+)\)\s+(?:similar|completed|works|projects)",
        r"(\d+)\s+(?:or\s+more\s+)?similar\s+works",
        r"(\d+)\s+completed\s+projects",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            val = m.group(1)
            if val.isdigit():
                return int(val)
            if val in _NUMBER_WORDS:
                return _NUMBER_WORDS[val]
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. Corpus building: split each bidder's documents into chunks
# ─────────────────────────────────────────────────────────────────────────────

CHUNK_SIZE = chunking.CHUNK_SIZE
CHUNK_OVERLAP = chunking.CHUNK_OVERLAP
DEFAULT_TOP_K = 8


def build_bidder_corpus(documents: list) -> list:
    """Convert a list of documents into a flat list of chunk dicts.

    Each chunk carries its source document name and OCR confidence so the
    downstream evidence record stays traceable.
    """
    corpus = []
    for doc in documents:
        text = doc.get("full_text", "") or ""
        if not text.strip():
            continue
        for i, chunk in enumerate(chunking.split_into_chunks(text)):
            corpus.append({
                "text": chunk,
                "source_document": doc.get("filename", "unknown"),
                "chunk_index": i,
                "ocr_confidence": doc.get("min_ocr_confidence"),
            })
    return corpus


# ─────────────────────────────────────────────────────────────────────────────
# 3. Keyword extraction + scoring (no hardcoded category maps)
# ─────────────────────────────────────────────────────────────────────────────

# Generic stopwords + boilerplate words common to tender criteria.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "from", "by", "is", "are", "was", "were", "be", "been", "being",
    "must", "should", "shall", "may", "can", "could", "would", "will",
    "have", "has", "had", "do", "does", "did", "this", "that", "these", "those",
    "as", "such", "any", "all", "each", "every", "no", "not", "than", "then",
    "if", "while", "where", "when", "what", "which", "who", "how", "why",
    "during", "above", "below", "over", "under", "between", "into", "through",
    "criterion", "criteria", "required", "mandatory", "valid", "submit",
    "bidder", "tender", "applicant", "vendor", "company", "firm",
    "etc", "i", "ii", "iii", "iv", "v", "vi", "vii",
    "their", "his", "her", "its", "our", "your", "they", "we", "you",
    "least", "minimum", "maximum", "more", "less", "value", "years",
    "year", "month", "months", "rs", "inr", "rupees",
}


def extract_keywords(text: str) -> set:
    """Pull meaningful tokens from a criterion's text.

    Intentionally simple: lowercase, alphanumeric tokens of length >= 3,
    minus stopwords. Numbers and short tokens that look like codes (e.g. "iso",
    "gst", "pan") are kept.
    """
    if not text:
        return set()
    text = text.lower()
    tokens = re.findall(r"[a-z]+\d*|\d+", text)
    keywords = set()
    for t in tokens:
        if t in _STOPWORDS:
            continue
        if len(t) >= 3 or t.isdigit():
            keywords.add(t)
    return keywords


def criterion_keywords(criterion: dict) -> set:
    """Combine criterion description + threshold + expected_evidence into a keyword set."""
    text = " ".join([
        criterion.get("description", ""),
        criterion.get("expected_evidence", ""),
        criterion.get("threshold", ""),
    ])
    return extract_keywords(text)


def score_chunk(chunk_text: str, keywords: set) -> int:
    """Number of distinct keywords that appear in the chunk."""
    if not keywords or not chunk_text:
        return 0
    low = chunk_text.lower()
    return sum(1 for k in keywords if k in low)


def retrieve_top_chunks_keyword(corpus: list, criterion: dict,
                                 k: int = DEFAULT_TOP_K) -> list:
    """Return the top-K chunks by keyword overlap (baseline; no embeddings).

    Falls back to the first K chunks if no chunk scores above zero.
    """
    if not corpus:
        return []
    keywords = criterion_keywords(criterion)
    if not keywords:
        return corpus[:k]

    scored = [(score_chunk(c["text"], keywords), c) for c in corpus]
    nonzero = [(s, c) for s, c in scored if s > 0]
    nonzero.sort(key=lambda t: t[0], reverse=True)

    if nonzero:
        return [c for _, c in nonzero[:k]]
    return corpus[:k]


def retrieve_top_chunks(
    corpus: list,
    criterion: dict,
    k: int = DEFAULT_TOP_K,
    bidder_id: int = None,
    docs: list = None,
    on_index_status=None,
) -> list:
    """Hybrid retrieval: Chroma vector index (summary + chunks) when available."""
    if bidder_id is not None and docs is not None:
        from modules import rag_index

        if rag_index.chroma_available():
            col = rag_index.ensure_vector_index(
                bidder_id, docs, corpus, on_status=on_index_status,
            )
            if col is not None:
                try:
                    return rag_index.retrieve_from_index(col, criterion, corpus, k=k)
                except Exception:
                    pass
    return retrieve_top_chunks_keyword(corpus, criterion, k=k)


# ─────────────────────────────────────────────────────────────────────────────
# 4. LLM-driven evidence extraction from retrieved chunks
# ─────────────────────────────────────────────────────────────────────────────

def _ocr_confidence_for_doc(chunks: list, doc_name: str) -> float:
    """Look up the OCR confidence (if any) for a given source document."""
    for c in chunks:
        if c["source_document"] == doc_name:
            return c.get("ocr_confidence")
    return None


def extract_evidence(criterion: dict, chunks: list) -> list:
    """Ask the LLM to extract evidence items for one criterion from its top chunks.

    Returns a list of evidence dicts. Empty list means no evidence found.

    Each evidence dict has:
      {found, extracted_value, normalized_value, source_text,
       source_document, ocr_confidence, notes}
    """
    if not chunks:
        return []

    context = "\n\n".join(
        f"[CHUNK {i+1} from {c['source_document']}]\n{c['text']}"
        for i, c in enumerate(chunks)
    )

    prompt_template = _load_prompt("extract_evidence.txt")
    prompt = prompt_template.replace("{criterion_description}", criterion.get("description", ""))
    prompt = prompt.replace("{threshold}", criterion.get("threshold", "N/A"))
    prompt = prompt.replace("{expected_evidence}", criterion.get("expected_evidence", ""))
    prompt = prompt.replace("{evidence_chunks}", context)

    system = (
        "You extract evidence for a single tender eligibility criterion from a few "
        "short text excerpts taken from a bidder's submitted documents. "
        "Only report what is actually present. Do not fabricate values. "
        "Return valid JSON only matching the requested schema."
    )

    try:
        result = llm.chat_json(prompt, system_prompt=system, fast=True, num_predict=1024)
    except Exception:
        return []

    raw_items = []
    if isinstance(result, dict):
        raw_items = result.get("evidence", [])
        if not isinstance(raw_items, list):
            raw_items = [raw_items] if raw_items else []
    elif isinstance(result, list):
        raw_items = result

    out = []
    fallback_doc = chunks[0]["source_document"] if chunks else ""
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if not item.get("found", True):
            continue
        source_doc = item.get("source_document") or fallback_doc
        ocr_conf = _ocr_confidence_for_doc(chunks, source_doc)
        out.append({
            "found": True,
            "extracted_value": item.get("extracted_value", ""),
            "normalized_value": item.get("normalized_value"),
            "source_text": item.get("source_text", ""),
            "source_document": source_doc,
            "ocr_confidence": ocr_conf,
            "notes": item.get("notes", ""),
        })
    return out


def _ordered_document_names(corpus: list, excluded_docs: set) -> list:
    """Stable order: first appearance in corpus (matches upload / filename order)."""
    ordered = []
    seen = set()
    for ch in corpus:
        d = ch.get("source_document") or "unknown"
        if d in excluded_docs or d in seen:
            continue
        seen.add(d)
        ordered.append(d)
    return ordered


def _ordered_document_names_for_criterion(
    corpus: list, criterion: dict, excluded_docs: set,
) -> list:
    """Order documents by keyword relevance; fall back to corpus order if all scores are zero."""
    keywords = criterion_keywords(criterion)
    best_score = {}
    for ch in corpus:
        d = ch.get("source_document") or "unknown"
        if d in excluded_docs:
            continue
        sc = score_chunk(ch.get("text") or "", keywords)
        prev = best_score.get(d)
        if prev is None or sc > prev:
            best_score[d] = sc
    if not best_score:
        return []
    if keywords and any(best_score[n] > 0 for n in best_score):
        return sorted(best_score.keys(), key=lambda n: (-best_score[n], n))
    return _ordered_document_names(corpus, excluded_docs)


def _evidence_row_dedup_key(row: dict, doc_name: str) -> tuple:
    """Key to avoid duplicate LLM rows; allow same rupee value from different docs/snippets."""
    src = row.get("source_document") or doc_name
    val = str(row.get("extracted_value", "")).strip()
    st = (row.get("source_text") or "")[:240].strip()
    return (src, val, st)


def extract_evidence_sequential_docs(
    corpus: list,
    criterion: dict,
    excluded_docs: set = None,
    batch_size: int = None,
) -> tuple:
    """Scan bidder documents one at a time until evidence is found or all are exhausted.

    For each document (skipping *excluded_docs*), run *extract_evidence* on consecutive
    chunk batches until a batch returns non-empty evidence or the document is exhausted.

    When the criterion requires **more than one** item (e.g. \"three similar works\"),
    **every** ordered document is scanned and evidence rows are merged (deduplicated),
    so completion certificates in separate files are all considered — not only the first
    file that returned something.

    Returns:
        (evidence_list, reserved_docs)
        *reserved_docs* — filenames that supplied evidence; callers may add these to
        *excluded_docs* for later criteria only when ``RESERVE_EVIDENCE_DOCS`` / env
        ``TENDERLENS_RESERVE_EVIDENCE_DOCS`` is enabled (off by default so one document
        can legitimately support more than one criterion).
    """
    excluded_docs = set(excluded_docs or ())
    batch_size = int(batch_size or DOC_SCAN_BATCH_SIZE)
    if batch_size < 1:
        batch_size = DEFAULT_TOP_K

    doc_names = _ordered_document_names_for_criterion(corpus, criterion, excluded_docs)
    required_n = parse_count_requirement(criterion.get("description", ""))
    merge_all_docs = required_n > 1

    if merge_all_docs:
        accumulated = []
        seen = set()
        reserved = set()
        for doc_name in doc_names:
            doc_chunks = [
                c for c in corpus
                if (c.get("source_document") or "unknown") == doc_name
            ]
            doc_chunks.sort(key=lambda c: c.get("chunk_index", 0))
            added_here = False
            for i in range(0, len(doc_chunks), batch_size):
                batch = doc_chunks[i:i + batch_size]
                ev = extract_evidence(criterion, batch)
                if not ev:
                    continue
                for row in ev:
                    key = _evidence_row_dedup_key(row, doc_name)
                    if key in seen:
                        continue
                    seen.add(key)
                    accumulated.append(row)
                    added_here = True
            if added_here:
                reserved.add(doc_name)
        if accumulated:
            return accumulated, reserved
        return [], set()

    for doc_name in doc_names:
        doc_chunks = [
            c for c in corpus
            if (c.get("source_document") or "unknown") == doc_name
        ]
        doc_chunks.sort(key=lambda c: c.get("chunk_index", 0))

        for i in range(0, len(doc_chunks), batch_size):
            batch = doc_chunks[i:i + batch_size]
            ev = extract_evidence(criterion, batch)
            if ev:
                reserved = {doc_name}
                for row in ev:
                    sd = row.get("source_document")
                    if sd:
                        reserved.add(sd)
                return ev, reserved

    return [], set()


# ─────────────────────────────────────────────────────────────────────────────
# 5. Convenience wrapper for callers that want everything done in one shot
# ─────────────────────────────────────────────────────────────────────────────

def process_bidder_documents(file_paths: list, criteria: list) -> dict:
    """Ingest, build corpus, and extract evidence for one bidder."""
    documents = [ingest_document(fp) for fp in file_paths]
    corpus = build_bidder_corpus(documents)

    evidence_map = {}
    used_docs = set()
    for criterion in criteria:
        cid = criterion.get("criterion_id") or criterion.get("id")
        excl = used_docs if RESERVE_EVIDENCE_DOCS else None
        ev, reserved = extract_evidence_sequential_docs(
            corpus, criterion, excluded_docs=excl,
        )
        evidence_map[cid] = ev
        if RESERVE_EVIDENCE_DOCS:
            used_docs |= reserved

    return {"documents": documents, "evidence": evidence_map}
