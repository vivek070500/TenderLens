"""Vector RAG index: document summaries + chunk embeddings in ChromaDB.

Dual-path retrieval (summary + chunks), query-time fusion with keyword fallback.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any, Callable, List, Optional

from config import (
    CHROMA_DIR,
    OLLAMA_EMBED_MODEL,
    RAG_DOC_SUMMARIES,
    RAG_ENABLED,
    RAG_SUMMARY_INPUT_CHARS,
    RAG_TOP_DOC_SUMMARIES,
    RAG_VECTOR_CHUNK_POOL,
)
from modules import chunking
from modules import llm

DEFAULT_TOP_K = 8
_chroma_lock = threading.Lock()


def _fingerprint_docs(docs: List[dict]) -> str:
    parts = []
    for d in sorted(docs, key=lambda x: (x.get("filename") or "", x.get("id") or 0)):
        fh = d.get("file_hash") or ""
        fn = d.get("filename") or ""
        ft = d.get("full_text") or ""
        parts.append(f"{fn}|{fh}|{len(ft)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8", errors="replace")).hexdigest()


def _meta_path(bidder_id: int) -> str:
    os.makedirs(CHROMA_DIR, exist_ok=True)
    return os.path.join(CHROMA_DIR, f"bidder_{bidder_id}.index.json")


def _load_meta(bidder_id: int) -> dict:
    p = _meta_path(bidder_id)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_meta(bidder_id: int, fp: str, embed_model: str):
    p = _meta_path(bidder_id)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fp, "embed_model": embed_model}, f, indent=0)


def _chromadb_client():
    import chromadb
    from chromadb.config import Settings

    os.makedirs(CHROMA_DIR, exist_ok=True)
    return chromadb.PersistentClient(
        path=CHROMA_DIR,
        settings=Settings(anonymized_telemetry=False),
    )


def _collection_name(bidder_id: int) -> str:
    return f"bidder_{bidder_id}"


def _summarize_document(filename: str, full_text: str) -> str:
    excerpt = (full_text or "")[:RAG_SUMMARY_INPUT_CHARS]
    if not excerpt.strip():
        return ""
    prompt = (
        f"Document filename: {filename}\n\n"
        f"Content (may be partial):\n{excerpt}\n\n"
        "Write ONE dense paragraph (max 120 words) optimized for semantic search over "
        "tender bid packets. Include: company identifiers, monetary amounts and currency, "
        "key dates, ISO/other certifications, GST/PAN/registration mentions, similar-work / "
        "project claims with values, technical personnel and experience, and any "
        "blacklisting, suspension, or litigation. No preamble — paragraph only."
    )
    try:
        out = llm.chat(
            prompt,
            system_prompt="You write factual search summaries only. No markdown.",
            fast=True,
        )
        return (out or "").strip()[:2000]
    except Exception:
        return excerpt[:1500]


def _safe_embed(text: str) -> Optional[List[float]]:
    try:
        return llm.embed_text(text)
    except Exception:
        return None


def chroma_available() -> bool:
    if not RAG_ENABLED:
        return False
    try:
        import chromadb  # noqa: F401
        return True
    except ImportError:
        return False


def ensure_vector_index(
    bidder_id: int,
    docs: List[dict],
    corpus: List[dict],
    on_status: Optional[Callable[[str], None]] = None,
):
    """Get or build a Chroma collection for this bidder. Returns collection or None."""
    if not chroma_available() or not docs:
        return None

    fp = _fingerprint_docs(docs)
    meta = _load_meta(bidder_id)
    client = _chromadb_client()
    name = _collection_name(bidder_id)

    with _chroma_lock:
        if meta.get("fingerprint") == fp and meta.get("embed_model") == OLLAMA_EMBED_MODEL:
            try:
                return client.get_collection(name)
            except Exception:
                pass

        if on_status:
            on_status("Building vector index (embeddings + summaries)…")
        try:
            try:
                client.delete_collection(name)
            except Exception:
                pass
            collection = client.create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception:
            return None

        ids: List[str] = []
        embeddings: List[List[float]] = []
        documents: List[str] = []
        metadatas: List[dict] = []

        if RAG_DOC_SUMMARIES:
            for di, doc in enumerate(docs):
                fn = doc.get("filename") or f"doc_{di}"
                text = doc.get("full_text") or ""
                summary = _summarize_document(fn, text)
                if not summary.strip():
                    continue
                emb = _safe_embed(summary)
                if emb is None:
                    if on_status:
                        on_status("Embed model unavailable — skipping vector index.")
                    try:
                        client.delete_collection(name)
                    except Exception:
                        pass
                    return None
                sid = f"b{bidder_id}_d{di}_summary"
                ids.append(sid)
                embeddings.append(emb)
                documents.append(summary)
                metadatas.append(
                    _chroma_metadata(
                        "doc_summary", fn, -1, doc.get("min_ocr_confidence"),
                    )
                )

        for di, doc in enumerate(docs):
            fn = doc.get("filename") or f"doc_{di}"
            text = doc.get("full_text") or ""
            if not text.strip():
                continue
            for ci, chunk in enumerate(chunking.split_into_chunks(text)):
                emb = _safe_embed(chunk)
                if emb is None:
                    if on_status:
                        on_status("Embed model unavailable — skipping vector index.")
                    try:
                        client.delete_collection(name)
                    except Exception:
                        pass
                    return None
                cid = f"b{bidder_id}_d{di}_c{ci}"
                ids.append(cid)
                embeddings.append(emb)
                documents.append(chunk)
                metadatas.append(
                    _chroma_metadata("chunk", fn, ci, doc.get("min_ocr_confidence")),
                )

        if not ids:
            try:
                client.delete_collection(name)
            except Exception:
                pass
            return None

        batch = 64
        for i in range(0, len(ids), batch):
            collection.add(
                ids=ids[i: i + batch],
                embeddings=embeddings[i: i + batch],
                documents=documents[i: i + batch],
                metadatas=metadatas[i: i + batch],
            )

        _save_meta(bidder_id, fp, OLLAMA_EMBED_MODEL)
        if on_status:
            on_status("Vector index ready.")
        return collection


def _meta_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _chroma_metadata(
    kind: str,
    source_document: str,
    chunk_index: int,
    ocr_raw: Any,
) -> dict:
    """Chroma only accepts str, int, float, bool — no None, no NumPy scalars."""
    ocr = -1.0
    if ocr_raw is not None:
        try:
            ocr = float(ocr_raw)
        except (TypeError, ValueError):
            ocr = -1.0
    return {
        "kind": str(kind),
        "source_document": str(source_document or ""),
        "chunk_index": int(chunk_index),
        "ocr_confidence": ocr,
    }


def _criterion_query_text(criterion: dict) -> str:
    parts = [
        criterion.get("description") or "",
        criterion.get("threshold") or "",
        criterion.get("expected_evidence") or "",
        criterion.get("category") or "",
    ]
    return "\n".join(p for p in parts if p).strip()


def _chunk_key(c: dict) -> tuple:
    return (c.get("source_document"), c.get("chunk_index"))


def retrieve_from_index(
    collection,
    criterion: dict,
    corpus: List[dict],
    k: int = DEFAULT_TOP_K,
) -> List[dict]:
    """Retrieve chunk dicts: vector hits on chunks + expansion from summary-matched docs."""
    from modules import bidder_processor as bp  # late import

    if collection is None or not corpus:
        return bp.retrieve_top_chunks_keyword(corpus, criterion, k=k)

    qtext = _criterion_query_text(criterion)
    if not qtext:
        return bp.retrieve_top_chunks_keyword(corpus, criterion, k=k)

    qemb = _safe_embed(qtext)
    if qemb is None:
        return bp.retrieve_top_chunks_keyword(corpus, criterion, k=k)

    n_chunk = min(RAG_VECTOR_CHUNK_POOL, max(k * 3, k))
    n_sum = RAG_TOP_DOC_SUMMARIES

    try:
        chunk_hit = collection.query(
            query_embeddings=[qemb],
            n_results=n_chunk,
            where={"kind": "chunk"},
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        return bp.retrieve_top_chunks_keyword(corpus, criterion, k=k)

    corpus_by_key = {_chunk_key(c): c for c in corpus}
    out: List[dict] = []
    seen = set()

    def _add_chunk_dict(ch: dict):
        key = _chunk_key(ch)
        if key in seen or None in key:
            return
        seen.add(key)
        out.append(ch)

    md = chunk_hit.get("metadatas") or [[]]
    docs = chunk_hit.get("documents") or [[]]
    if md and docs and md[0] and docs[0]:
        for text, meta in zip(docs[0], md[0]):
            if not meta or meta.get("kind") != "chunk":
                continue
            fn = meta.get("source_document") or ""
            ci = int(meta.get("chunk_index") or 0)
            ck = (fn, ci)
            if ck in corpus_by_key:
                _add_chunk_dict(corpus_by_key[ck])
            else:
                ocr_m = meta.get("ocr_confidence")
                try:
                    ocr_f = float(ocr_m) if ocr_m is not None else None
                except (TypeError, ValueError):
                    ocr_f = None
                ocr_out = None if ocr_f is None or ocr_f < 0 else ocr_f
                _add_chunk_dict({
                    "text": text,
                    "source_document": fn,
                    "chunk_index": ci,
                    "ocr_confidence": ocr_out,
                })

    boosted_docs = set()
    if n_sum > 0 and RAG_DOC_SUMMARIES:
        try:
            sum_hit = collection.query(
                query_embeddings=[qemb],
                n_results=n_sum,
                where={"kind": "doc_summary"},
                include=["metadatas", "distances"],
            )
            smd = sum_hit.get("metadatas") or [[]]
            if smd and smd[0]:
                for meta in smd[0]:
                    if meta and meta.get("source_document"):
                        boosted_docs.add(meta["source_document"])
        except Exception:
            pass

    for fn in boosted_docs:
        added = 0
        for c in corpus:
            if c.get("source_document") != fn:
                continue
            key = _chunk_key(c)
            if key in seen:
                continue
            _add_chunk_dict(c)
            added += 1
            if added >= 2:
                break

    if len(out) < k:
        for c in bp.retrieve_top_chunks_keyword(corpus, criterion, k=max(k * 2, k)):
            key = _chunk_key(c)
            if key in seen:
                continue
            _add_chunk_dict(c)
            if len(out) >= k:
                break

    return out[:k]


def delete_bidder_index(bidder_id: int):
    """Remove Chroma collection and index metadata for a bidder."""
    try:
        import chromadb
        from chromadb.config import Settings

        client = chromadb.PersistentClient(
            path=CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
        client.delete_collection(_collection_name(bidder_id))
    except Exception:
        pass
    mp = _meta_path(bidder_id)
    if os.path.isfile(mp):
        try:
            os.remove(mp)
        except OSError:
            pass
