import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "phi3:mini"
OLLAMA_TIMEOUT = 120

UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
SAMPLE_DATA_DIR = os.path.join(BASE_DIR, "sample_data")
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
DB_PATH = os.path.join(BASE_DIR, "database", "tenderlens.db")

OCR_CONFIDENCE_HIGH = 85
OCR_CONFIDENCE_MEDIUM = 65

VERDICT_ELIGIBLE = "ELIGIBLE"
VERDICT_NOT_ELIGIBLE = "NOT_ELIGIBLE"
VERDICT_NEEDS_REVIEW = "NEEDS_REVIEW"

BORDERLINE_THRESHOLD_PERCENT = 10

os.makedirs(UPLOADS_DIR, exist_ok=True)
