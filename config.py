import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_FAST_MODEL = os.environ.get("OLLAMA_FAST_MODEL", OLLAMA_MODEL)
OLLAMA_TIMEOUT = 120
# Local embeddings via Ollama (pull once: `ollama pull nomic-embed-text`)
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

LLM_KEEP_ALIVE = "30m"
MAX_PARALLEL_LLM = int(os.environ.get("MAX_PARALLEL_LLM", "4"))

# Vector RAG (ChromaDB on disk). Set TENDERLENS_RAG=0 to disable.
RAG_ENABLED = os.environ.get("TENDERLENS_RAG", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
RAG_DOC_SUMMARIES = os.environ.get("TENDERLENS_RAG_SUMMARIES", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
RAG_SUMMARY_INPUT_CHARS = int(os.environ.get("TENDERLENS_RAG_SUMMARY_CHARS", "12000"))
RAG_TOP_DOC_SUMMARIES = int(os.environ.get("TENDERLENS_RAG_TOP_SUMMARIES", "4"))
RAG_VECTOR_CHUNK_POOL = int(os.environ.get("TENDERLENS_RAG_CHUNK_POOL", "24"))

# Anthropic-style contextual retrieval: LLM situates each chunk + hybrid BM25 + Chroma + optional rerank.
RAG_CONTEXTUAL_RETRIEVAL = os.environ.get("TENDERLENS_RAG_CONTEXTUAL", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
RAG_CONTEXTUAL_BATCH_SIZE = int(os.environ.get("TENDERLENS_RAG_CONTEXT_BATCH", "6"))
RAG_CONTEXTUAL_MAX_DOC_CHARS = int(os.environ.get("TENDERLENS_RAG_CONTEXT_DOC_CHARS", "12000"))
RAG_HYBRID_RERANK = os.environ.get("TENDERLENS_RAG_HYBRID_RERANK", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# Evidence extraction: by default scan each bidder document in order (chunk batches)
# instead of Chroma/top-K retrieval. Set TENDERLENS_EVIDENCE_RAG=1 to use vector retrieval.
EVIDENCE_USE_RAG_RETRIEVAL = os.environ.get("TENDERLENS_EVIDENCE_RAG", "0").strip().lower() in (
    "1", "true", "yes", "on",
)
# Chunks per LLM call when scanning a single document (larger = more context, more tokens).
DOC_SCAN_BATCH_SIZE = int(os.environ.get("TENDERLENS_DOC_SCAN_BATCH", "12"))
# If true: after a file yields evidence for one criterion, skip that file for later criteria
# for the same bidder (fewer LLM calls). Default off so one PDF (e.g. combined CA cert) can
# still support multiple criteria. When off, the same doc may be scanned again under other criteria.
RESERVE_EVIDENCE_DOCS = os.environ.get("TENDERLENS_RESERVE_EVIDENCE_DOCS", "0").strip().lower() in (
    "1", "true", "yes", "on",
)

UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
SAMPLE_DATA_DIR = os.path.join(BASE_DIR, "sample_data")
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
DB_PATH = os.path.join(BASE_DIR, "database", "tenderlens.db")
CHROMA_DIR = os.path.join(BASE_DIR, "database", "chroma")

# Default: session-style workspace — SQLite is cleared when you start a new browser session
# and again when you submit a new procurement. Set TENDERLENS_PERSIST=1 to keep tenders
# and evaluations on disk across visits (multi-tender history).
PERSIST_WORKSPACE = os.environ.get("TENDERLENS_PERSIST", "0").strip().lower() in (
    "1", "true", "yes", "on",
)

OCR_CONFIDENCE_HIGH = 85
OCR_CONFIDENCE_MEDIUM = 65

VERDICT_ELIGIBLE = "ELIGIBLE"
VERDICT_NOT_ELIGIBLE = "NOT_ELIGIBLE"
VERDICT_NEEDS_REVIEW = "NEEDS_REVIEW"

BORDERLINE_THRESHOLD_PERCENT = 10

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)
