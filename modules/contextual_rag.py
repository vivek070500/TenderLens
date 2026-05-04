"""
Anthropic-style contextual retrieval helpers: contextualize chunks before index,
hybrid vector + BM25, optional LLM rerank. Works with Ollama + Chroma + rank_bm25.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from config import (
    CHROMA_DIR,
    RAG_CONTEXTUAL_BATCH_SIZE,
    RAG_CONTEXTUAL_MAX_DOC_CHARS,
    RAG_HYBRID_RERANK,
    RAG_CONTEXTUAL_RETRIEVAL,
)
from modules import chunking, llm

CONTEXT_PROMPT_VER = "v1"

# --- 1) Chunking -----------------------------------------------------------------


def chunk_documents(docs: List[dict], bidder_id: int) -> List[dict]:
    """Split bidder documents into chunk records with stable chunk_id."""
    out: List[dict] = []
    for di, doc in enumerate(docs):
        fn = doc.get("filename") or f"doc_{di}"
        text = doc.get("full_text") or ""
        if not text.strip():
            continue
        for ci, chunk in enumerate(chunking.split_into_chunks(text)):
            cid = f"b{bidder_id}_d{di}_c{ci}"
            out.append({
                "chunk_id": cid,
                "doc_index": di,
                "chunk_index": ci,
                "source_document": fn,
                "text": chunk,
                "full_doc_text": text,
                "ocr_confidence": doc.get("min_ocr_confidence"),
            })
    return out


# --- 2) Contextualization ---------------------------------------------------------


def _context_cache_dir() -> str:
    d = os.path.join(CHROMA_DIR, "context_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_key(bidder_id: int, chunk_id: str, doc_fp: str, chunk_text: str) -> str:
    h = hashlib.sha256(
        f"{CONTEXT_PROMPT_VER}|{bidder_id}|{chunk_id}|{doc_fp}|{chunk_text}".encode(
            "utf-8", errors="replace"
        )
    ).hexdigest()
    return h


def _read_cache(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = f.read().strip()
        return s if s else None
    except OSError:
        return None


def _write_cache(path: str, text: str):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def format_contextualized(context: str, original_chunk: str) -> str:
    """Anthropic-style string embedded and indexed for BM25 + vectors."""
    ctx = (context or "").strip() or "General excerpt from the bid document."
    return f"Context: {ctx}\nChunk: {original_chunk}"


def generate_context(
    chunk_text: str,
    full_doc_excerpt: str,
    filename: str,
    *,
    bidder_id: int,
    chunk_id: str,
    doc_fingerprint: str,
) -> str:
    """Single-chunk LLM context (used as fallback; prefer batch)."""
    ck = _cache_key(bidder_id, chunk_id, doc_fingerprint, chunk_text)
    cp = os.path.join(_context_cache_dir(), f"{ck}.txt")
    hit = _read_cache(cp)
    if hit is not None:
        return hit
    excerpt = (full_doc_excerpt or "")[:RAG_CONTEXTUAL_MAX_DOC_CHARS]
    prompt = (
        f"Document filename: {filename}\n\n"
        f"Document excerpt (for grounding only):\n{excerpt}\n\n"
        f"Target chunk:\n{chunk_text[:3500]}\n\n"
        "Write ONE concise sentence of context that situates this chunk in the document: "
        "entities (company, certifying authority, project names), dates, monetary amounts, "
        "document type (e.g. turnover certificate, completion certificate), and relationships "
        "to other facts in the excerpt. No preamble — sentence only."
    )
    try:
        out = llm.chat(
            prompt,
            system_prompt=(
                "You write factual situating sentences for search indexing in tender/bid packets. "
                "No markdown, no quotes wrapping the whole answer."
            ),
            fast=True,
        ).strip()
    except Exception:
        out = f"Excerpt from {filename} in bidder submission."
    _write_cache(cp, out)
    return out


def _contexts_from_batch_llm(
    filename: str,
    full_excerpt: str,
    chunk_texts: List[str],
) -> List[str]:
    numbered = "\n".join(
        f"[{i + 1}] {(c or '')[:1800]}"
        for i, c in enumerate(chunk_texts)
    )
    excerpt = (full_excerpt or "")[:RAG_CONTEXTUAL_MAX_DOC_CHARS]
    prompt = (
        f"Document filename: {filename}\n\n"
        f"Full-document excerpt for grounding:\n{excerpt}\n\n"
        f"Chunks (maintain order — one context per line index):\n{numbered}\n\n"
        'Return JSON: {"contexts": ["situate chunk 1 in one sentence", "..."]} '
        f"Exactly {len(chunk_texts)} strings. Each sentence: entities, dates, amounts, "
        "doc type, relationships (tender/bid domain). No markdown."
    )
    try:
        raw = llm.chat_json(
            prompt,
            system_prompt="Return only valid JSON with key contexts (array of strings).",
            fast=True,
            num_predict=2048,
        )
        arr = list(raw.get("contexts") or [])
    except Exception:
        arr = []
    out: List[str] = []
    for i in range(len(chunk_texts)):
        if i < len(arr) and str(arr[i]).strip():
            out.append(str(arr[i]).strip())
        else:
            out.append(f"Content from {filename} in the bidder packet (chunk {i + 1}).")
    return out


def generate_contexts_for_doc_batches(
    doc_chunks: List[dict],
    bidder_id: int,
    doc_fingerprint: str,
    batch_size: int = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """Batched contextualization for chunks of one document (same doc_index)."""
    if not doc_chunks:
        return []
    bs = batch_size or RAG_CONTEXTUAL_BATCH_SIZE
    filename = doc_chunks[0]["source_document"]
    full_doc = doc_chunks[0]["full_doc_text"]
    contexts: List[str] = []

    for start in range(0, len(doc_chunks), bs):
        batch = doc_chunks[start: start + bs]
        # per-chunk cache short-circuit
        to_run_idx: List[int] = []
        batch_ctx = [""] * len(batch)
        for i, b in enumerate(batch):
            ck = _cache_key(bidder_id, b["chunk_id"], doc_fingerprint, b["text"])
            cp = os.path.join(_context_cache_dir(), f"{ck}.txt")
            hit = _read_cache(cp)
            if hit is not None:
                batch_ctx[i] = hit
            else:
                to_run_idx.append(i)
        if to_run_idx:
            if on_status and start == 0:
                on_status(f"Contextualizing chunks ({filename})…")
            sub_texts = [batch[i]["text"] for i in to_run_idx]
            generated = _contexts_from_batch_llm(filename, full_doc, sub_texts)
            for j, idx in enumerate(to_run_idx):
                ctext = generated[j] if j < len(generated) else batch[idx]["text"][:200]
                batch_ctx[idx] = ctext
                ck = _cache_key(
                    bidder_id, batch[idx]["chunk_id"], doc_fingerprint, batch[idx]["text"]
                )
                _write_cache(os.path.join(_context_cache_dir(), f"{ck}.txt"), ctext)
        contexts.extend(batch_ctx)
    return contexts


def build_contextualized_records(
    chunked: List[dict],
    bidder_id: int,
    doc_fingerprint: str,
    on_status: Optional[Callable[[str], None]] = None,
) -> Tuple[List[str], List[str], List[dict]]:
    """
    Returns parallel lists: contextualized_strings (for embed + BM25), originals, metas.
    """
    if not RAG_CONTEXTUAL_RETRIEVAL:
        contextualized = [format_contextualized("", c["text"]) for c in chunked]
        metas = [_meta_from_chunk(c) for c in chunked]
        originals = [c["text"] for c in chunked]
        return contextualized, originals, metas

    # Group by doc_index to batch LLM per document
    by_doc: Dict[int, List[dict]] = {}
    for c in chunked:
        by_doc.setdefault(c["doc_index"], []).append(c)
    id_to_context: Dict[str, str] = {}

    for di in sorted(by_doc.keys()):
        rows = sorted(by_doc[di], key=lambda x: x["chunk_index"])
        ctxs = generate_contexts_for_doc_batches(
            rows, bidder_id, doc_fingerprint, on_status=on_status
        )
        for row, ctx in zip(rows, ctxs):
            id_to_context[row["chunk_id"]] = ctx

    contextualized_strings: List[str] = []
    originals: List[str] = []
    metas: List[dict] = []
    for c in chunked:
        ctx = id_to_context.get(c["chunk_id"], "")
        contextualized_strings.append(format_contextualized(ctx, c["text"]))
        originals.append(c["text"])
        metas.append(_meta_from_chunk(c))
    return contextualized_strings, originals, metas


def _meta_from_chunk(c: dict) -> dict:
    ocr = c.get("ocr_confidence")
    ocr_f = -1.0
    if ocr is not None:
        try:
            ocr_f = float(ocr)
        except (TypeError, ValueError):
            ocr_f = -1.0
    return {
        "chunk_id": c["chunk_id"],
        "source_document": c.get("source_document") or "",
        "chunk_index": int(c.get("chunk_index", 0)),
        "ocr_confidence": ocr_f,
    }


# --- 3) BM25 ----------------------------------------------------------------------


def _tokenize(s: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def build_bm25_index(contextualized_chunks: Sequence[str]):
    """Build in-memory BM25 over contextualized text (same strings as embedded)."""
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return None, []
    tok = [_tokenize(t) for t in contextualized_chunks]
    if not tok or all(not t for t in tok):
        return None, tok
    return BM25Okapi(tok), tok


def bm25_top_ids(
    bm25,
    tokenized_corpus: List[List[str]],
    query: str,
    top_k: int,
    chunk_ids: Sequence[str],
) -> List[str]:
    if bm25 is None or not query.strip():
        return []
    q = _tokenize(query)
    if not q:
        return []
    scores = bm25.get_scores(q)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    out: List[str] = []
    for i in ranked[:top_k]:
        if 0 <= i < len(chunk_ids):
            out.append(chunk_ids[i])
    return out


# --- 4) Hybrid fusion -------------------------------------------------------------


def reciprocal_rank_fusion(
    ranked_lists: List[List[str]],
    k: int = 60,
) -> List[str]:
    """RRF merge; dedupe preserves first-seen order by decreasing score."""
    scores: Dict[str, float] = {}
    for rlist in ranked_lists:
        for rank, cid in enumerate(rlist):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)


def hybrid_retrieve(
    collection,
    query_text: str,
    query_embedding: List[float],
    chunk_ids: List[str],
    bm25_index,
    tokenized_corpus: List[List[str]],
    vector_top_k: int,
    bm25_top_k: int,
) -> Tuple[List[str], List[str], List[str]]:
    """
    Vector query + BM25 query → RRF merge → ordered chunk_ids.
    Returns (merged_ids, vector_only_ids, bm25_only_ids) for debugging.
    """
    vec_ids: List[str] = []
    if collection is not None and query_embedding:
        try:
            res = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(vector_top_k, max(1, len(chunk_ids))),
                where={"kind": "chunk"},
                include=["metadatas", "distances"],
            )
            md = (res.get("metadatas") or [[]])[0]
            for m in md:
                if m and m.get("chunk_id"):
                    vec_ids.append(str(m["chunk_id"]))
        except Exception:
            pass

    bm25_ids = bm25_top_ids(
        bm25_index, tokenized_corpus, query_text, bm25_top_k, chunk_ids
    )
    merged = reciprocal_rank_fusion([vec_ids, bm25_ids])
    return merged, vec_ids, bm25_ids


# --- 5) Reranking -----------------------------------------------------------------


def rerank_results(
    query: str,
    chunk_dicts: List[dict],
    top_n: int,
    *,
    use_llm: Optional[bool] = None,
) -> List[dict]:
    """LLM scores relevance of each chunk (original text) to the query; keep top_n."""
    if use_llm is None:
        use_llm = RAG_HYBRID_RERANK
    if not use_llm or len(chunk_dicts) <= 1:
        return chunk_dicts[:top_n]

    cap = min(12, len(chunk_dicts))
    subset = chunk_dicts[:cap]
    lines = []
    for i, c in enumerate(subset):
        t = (c.get("text") or "")[:900]
        lines.append(f"{i}: [{c.get('source_document')}#{(c.get('chunk_index'))}]\n{t}")
    block = "\n\n".join(lines)
    prompt = (
        f"Eligibility criterion / query:\n{query[:2500]}\n\n"
        f"Evidence chunks (indices 0..{cap - 1}):\n{block}\n\n"
        'Return JSON: {"scores": [{"i": 0, "score": 7.5}, ...]} '
        "score is 0-10 relevance for satisfying or addressing the query. "
        f"Include one entry per index 0..{cap - 1}."
    )
    try:
        raw = llm.chat_json(
            prompt,
            system_prompt="Return only valid JSON. Be concise.",
            fast=True,
            num_predict=1024,
        )
        arr = list(raw.get("scores") or [])
        by_i: Dict[int, float] = {}
        for it in arr:
            try:
                ii = int(it.get("i"))
                sc = float(it.get("score", 0))
                by_i[ii] = sc
            except (TypeError, ValueError):
                continue
        order = sorted(range(len(subset)), key=lambda j: by_i.get(j, 0.0), reverse=True)
        reranked = [subset[j] for j in order]
        rest = chunk_dicts[cap:]
        return (reranked + rest)[:top_n]
    except Exception:
        return chunk_dicts[:top_n]


# --- 6) Sidecar persistence -------------------------------------------------------


def hybrid_index_path(bidder_id: int) -> str:
    os.makedirs(CHROMA_DIR, exist_ok=True)
    return os.path.join(CHROMA_DIR, f"bidder_{bidder_id}.hybrid.json")


def save_hybrid_sidecar(
    bidder_id: int,
    chunk_ids: List[str],
    contextualized: List[str],
    originals: List[str],
    metas: List[dict],
):
    payload = {
        "version": 1,
        "context_prompt_ver": CONTEXT_PROMPT_VER,
        "chunk_ids": chunk_ids,
        "contextualized": contextualized,
        "originals": originals,
        "metas": metas,
    }
    p = hybrid_index_path(bidder_id)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def load_hybrid_sidecar(bidder_id: int) -> Optional[dict]:
    p = hybrid_index_path(bidder_id)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
