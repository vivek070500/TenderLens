# TenderLens

**AI-Powered Eligibility Analysis for Government Procurement**

TenderLens is a platform that helps procurement officers evaluate tender bids. You upload a tender document and bidder submissions, and the system automatically extracts eligibility criteria, parses every bidder's documents, evaluates each bidder against each criterion, and produces an auditable report. It runs 100% locally on your machine — no cloud APIs, no data leaves your laptop.

---

## What It Does

1. **Upload** a tender document (PDF, TXT, or DOCX) and bidder submissions (PDFs, images, Word files)
2. **Extracts** eligibility criteria from the tender using a local LLM
3. **Parses** every bidder's documents — handles typed PDFs, scanned documents, and even phone photographs via OCR
4. **Evaluates** each bidder against each criterion and assigns a verdict: Eligible, Not Eligible, or Needs Manual Review
5. **Produces** a consolidated report with explanations, officer override capability, and a full audit trail
6. **Exports** the report as a downloadable PDF

---

## Architecture

The platform is a five-stage pipeline. Each stage feeds into the next, and every action is logged for auditability.

```
                TENDER DOCUMENT                    BIDDER SUBMISSIONS
                      |                                    |
                      v                                    v
            +-------------------+              +------------------------+
            |  DOCUMENT         |              |  DOCUMENT              |
            |  INGESTION        |              |  INGESTION             |
            |  (PDF/DOCX/IMG)   |              |  (PDF/DOCX/IMG/OCR)   |
            +--------+----------+              +-----------+------------+
                     |                                     |
                     v                                     |
            +-------------------+                          |
            |  TENDER ANALYSIS  |                          |
            |  (LLM extracts    |                          |
            |   criteria)       |                          |
            +--------+----------+                          |
                     |                                     |
                     v                                     |
            +-------------------+                          |
            |  OFFICER REVIEW   |                          |
            |  (confirm/edit    |                          |
            |   criteria)       |                          |
            +--------+----------+                          |
                     |                   +-----------------+
                     |                   |
                     v                   v
            +-------------------------------------+
            |  BIDDER DOCUMENT PROCESSING          |
            |  (classify docs, extract evidence    |
            |   per criterion, normalize values)   |
            +------------------+------------------+
                               |
                               v
            +-------------------------------------+
            |  EVALUATION ENGINE                   |
            |  (quantitative: deterministic rules  |
            |   qualitative: LLM-assisted)         |
            +------------------+------------------+
                               |
                               v
            +-------------------------------------+
            |  REPORTING & HUMAN REVIEW            |
            |  (consolidated report, overrides,    |
            |   audit trail, PDF export)           |
            +-------------------------------------+
```

### Data Flow

- **Ingestion** converts all documents (any format) into structured text with provenance metadata (source file, page, OCR confidence)
- **Tender Analysis** sends the full tender text to the LLM and gets back structured criteria as JSON
- **Officer Review** lets the procurement officer confirm, edit, or add criteria before evaluation starts
- **Bidder Processing** classifies each document, then extracts evidence per criterion with value normalization
- **Evaluation** uses deterministic rules for numeric thresholds and LLM for qualitative judgments
- **Reporting** produces a consolidated matrix, per-bidder detail, and a full audit trail

---

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| **Language** | Python 3.11+ | Best ecosystem for document processing, OCR, and LLM integration |
| **UI Framework** | Streamlit | Fast to build, multi-page app support, built-in widgets for forms and tables |
| **Local LLM** | Ollama + Phi-3 Mini (3.8B) | Runs on 8GB Mac, no API keys needed, good at structured extraction |
| **OCR** | Tesseract via pytesseract | Lightweight (~50MB), reads scanned docs and photos, per-word confidence scores |
| **PDF Parsing** | PyMuPDF (fitz) | Fast text extraction from digital PDFs, can render pages as images for OCR fallback |
| **PDF Tables** | pdfplumber | Specialized table extraction from PDFs — critical for financial statements |
| **Word Files** | python-docx | Reads .docx files including tables |
| **Image Processing** | Pillow | Preprocessing before OCR: grayscale, contrast enhancement, sharpening |
| **Database** | SQLite | Zero-config, file-based, perfect for local app with audit trail |
| **PDF Reports** | FPDF2 | Generates multi-page PDF reports for download |
| **Data Display** | Pandas | DataFrames for the evaluation matrix display in Streamlit |

---

## Module-by-Module Breakdown

### `modules/llm.py` — LLM Wrapper

Connects to Ollama running locally on `http://localhost:11434`. Provides two functions:
- `chat(prompt, system_prompt)` — sends a prompt and gets a text response
- `chat_json(prompt, system_prompt)` — sends a prompt with JSON mode enabled, parses the response into a Python dict

Includes retry logic (2 retries with 2-second delay), timeout handling, and clear error messages if Ollama isn't running or the model isn't downloaded.

### `modules/ingestion.py` — Document Parser

Detects file type and routes to the right parser:
- **Digital PDFs** — extracts text via PyMuPDF, tables via pdfplumber
- **Scanned PDFs** — detects pages with < 50 characters of text, renders them as 300 DPI images, and sends to OCR
- **Word files** — extracts paragraphs and tables via python-docx
- **Images (JPG/PNG)** — sends directly to OCR
- **Text files** — reads directly

Every extracted document gets tagged with provenance: filename, SHA-256 hash, page number, OCR confidence per page.

### `modules/ocr.py` — OCR Pipeline

Wraps Tesseract with image preprocessing:
1. Convert to grayscale
2. Increase contrast (2x enhancement)
3. Apply sharpening filter
4. Run Tesseract OCR

Returns the extracted text plus an average confidence score (from Tesseract's per-word confidence). This confidence drives downstream decisions — low confidence values trigger "Needs Manual Review" verdicts instead of auto-decisions.

### `modules/tender_analyzer.py` — Criteria Extraction

Takes the full tender text and sends it to the LLM with a structured prompt. The LLM returns a JSON array of criteria, each with:
- Criterion ID (e.g., C-001)
- Description in plain language
- Category (financial / experience / compliance / technical)
- Mandatory or optional flag
- Numeric threshold if applicable
- Expected evidence documents
- Source section reference in the tender

### `modules/bidder_processor.py` — Evidence Extraction

Processes each bidder's documents in two passes:

**Pass 1 — Document Classification:** Sends the first 2000 characters of each document to the LLM. The LLM classifies it into one of 13 categories (financial_statement, gst_certificate, work_completion_certificate, iso_certificate, etc.).

**Pass 2 — Criterion-Guided Extraction:** For each criterion, sends the relevant document text to the LLM and asks it to extract the specific value or information that relates to that criterion. The LLM returns the extracted value, a normalized numeric value (if applicable), and the exact source text.

Also includes a value normalizer for Indian currency formats — handles "Rs. 5,20,00,000", "5.2 Crore", "Rupees Five Crore Twenty Lakhs", "INR 52000000", etc., and converts them all to a standard numeric form.

### `modules/evaluator.py` — Verdict Engine

Two evaluation modes:

**Deterministic (quantitative criteria):** For criteria with numeric thresholds (turnover >= Rs. 5 Cr, projects >= 3, etc.), the system does pure arithmetic comparison. No LLM involved. It also checks for borderline values (within 10% of threshold) and low OCR confidence — both trigger "Needs Review" instead of a hard verdict.

**LLM-Assisted (qualitative criteria):** For criteria requiring semantic judgment ("similar nature of work", "relevant experience"), the LLM assesses the evidence and returns a verdict with reasoning. The prompt instructs it to be conservative — when in doubt, choose "Needs Review".

Three possible verdicts per criterion:
- `ELIGIBLE` — clear, high-confidence evidence that the criterion is met
- `NOT_ELIGIBLE` — clear, high-confidence evidence that the criterion is NOT met
- `NEEDS_REVIEW` — ambiguous, incomplete, low-confidence, or borderline

The overall bidder verdict follows a strict rule: any mandatory criterion that is NOT_ELIGIBLE makes the bidder NOT_ELIGIBLE. Any mandatory criterion that NEEDS_REVIEW makes the bidder NEEDS_REVIEW. Only if all mandatory criteria are ELIGIBLE is the bidder ELIGIBLE.

### `modules/reporter.py` — PDF Report Generator

Generates a multi-section PDF report using FPDF2:
1. **Cover page** — tender name, date, criteria count, bidder count
2. **Criteria summary** — all criteria with categories and thresholds
3. **Consolidated results table** — all bidders with pass/fail/review counts and overall verdict
4. **Per-bidder detail** — criterion-by-criterion explanation with source references
5. **Officer overrides** — any manual verdict changes with reasons and officer names
6. **Audit trail** — every logged action with timestamps

### `database/db.py` — SQLite Operations

Eight tables with full CRUD operations:
- `tenders` — tender metadata
- `criteria` — extracted criteria linked to a tender
- `bidders` — bidder names linked to a tender
- `documents` — uploaded documents with provenance (hash, type, OCR confidence)
- `evidence` — extracted evidence per bidder per criterion
- `verdicts` — evaluation results per bidder per criterion
- `officer_overrides` — manual review decisions with reasons
- `audit_log` — every action timestamped for the audit trail

---

## LLM Prompt Strategy

All prompts are stored as text files in the `prompts/` folder. The strategy has three principles:

### 1. JSON-Mode Output

Every LLM call uses Ollama's JSON format mode (`format: "json"`). This forces the model to return valid JSON instead of free-form text. It prevents hallucinated prose and makes parsing reliable.

### 2. Few-Shot Examples

Each prompt includes 1-2 examples of the expected input/output format embedded in the prompt text itself. This grounds the model's output structure and reduces format errors.

### 3. Conservative Instructions

Every prompt includes explicit instructions to be conservative:
- "If you are not sure, say 'uncertain'. Do not make up information."
- "Only extract information that is ACTUALLY present in the document text."
- "If the information is not found in this document, set 'found' to false."
- "When in doubt, choose NEEDS_REVIEW."

This ensures the system errs on the side of flagging for human review rather than making a wrong automated call.

### The Four Prompts

| Prompt File | Used By | Purpose |
|-------------|---------|---------|
| `extract_criteria.txt` | `tender_analyzer.py` | Reads the tender and extracts all eligibility criteria as structured JSON |
| `classify_document.txt` | `bidder_processor.py` | Classifies a bidder document into one of 13 categories |
| `extract_evidence.txt` | `bidder_processor.py` | Extracts the specific value from a document that relates to a given criterion |
| `evaluate_criterion.txt` | `evaluator.py` | Evaluates whether extracted evidence meets a qualitative criterion |

---

## Prerequisites

You need three things installed on your Mac before you can run TenderLens:

### 1. Python 3.11 or higher

Check if you already have it:

```bash
python3 --version
```

If not installed or below 3.11:

```bash
brew install python@3.11
```

### 2. Ollama (Local LLM Runtime)

Ollama lets you run AI models locally on your Mac. Install it:

```bash
brew install ollama
```

After installing, pull the AI model we use (Phi-3 Mini — 2.3GB download):

```bash
ollama pull phi3:mini
```

This download takes a few minutes. Once done, start the Ollama server:

```bash
ollama serve
```

**Keep this terminal open.** Ollama needs to be running while you use TenderLens.

### 3. Tesseract OCR (For Scanned Documents)

Tesseract reads text from scanned documents and photographs:

```bash
brew install tesseract
```

Verify it installed:

```bash
tesseract --version
```

---

## Setup (One Time)

Open a **new terminal** (keep the Ollama terminal running) and run:

```bash
# Go to the tenderlens folder
cd tenderlens

# Create a virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

That's it. You're ready to run.

---

## How to Run

Make sure:
- Ollama is running in another terminal (`ollama serve`)
- Your virtual environment is activated (`source venv/bin/activate`)

Then:

```bash
streamlit run app.py
```

This opens TenderLens in your browser at `http://localhost:8501`.

---

## How to Use (Step by Step)

### Step 1: Upload Documents

You have two options:

**Option A: Use the included sample data (recommended for first run)**
- Click the "Use Sample Data" tab
- Click "Load Sample Data"
- This loads a sample CRPF construction tender with 10 mock bidders covering various scenarios (clear eligible, clear rejected, borderline, poor scans, missing documents, etc.)

**Option B: Upload your own documents**
- Enter a tender name
- Upload the tender document (PDF, TXT, or DOCX)
- For each bidder, enter their name and upload their submission files
- Click "Process All Documents"

The system will:
- Parse all documents (using OCR for scanned files and images)
- Send the tender text to the local LLM to extract eligibility criteria
- This takes 1-3 minutes depending on your machine

### Step 2: Review Criteria

- The system shows you the eligibility criteria it extracted from the tender
- Review each criterion — you can edit the description, threshold, category, and mandatory/optional flag
- Add any criteria the system missed
- Click "Confirm Criteria & Proceed to Evaluation"

### Step 3: Evaluation

- Click "Run Evaluation"
- The system evaluates each bidder against each criterion
- This takes several minutes (it makes one LLM call per bidder per criterion)
- Results appear as a colour-coded matrix:
  - Green = Eligible
  - Red = Not Eligible
  - Yellow = Needs Manual Review
- Click on any bidder to see the detailed explanation for each criterion
- For items marked "Needs Review", you can submit an officer override with a reason

### Step 4: Report

- See the consolidated report with all bidders and verdicts
- View the audit trail of every action
- Click "Generate PDF Report" to download a PDF you can print and file

---

## Project Structure

```
tenderlens/
├── app.py                     # Main Streamlit app (home page)
├── config.py                  # Configuration (model, paths, thresholds)
├── requirements.txt           # Python dependencies
├── modules/
│   ├── llm.py                 # Ollama LLM wrapper (chat + JSON mode)
│   ├── ingestion.py           # Document parser (PDF, DOCX, TXT, images)
│   ├── ocr.py                 # Tesseract OCR with image preprocessing
│   ├── tender_analyzer.py     # Extracts criteria from tender via LLM
│   ├── bidder_processor.py    # Classifies and extracts evidence from bidder docs
│   ├── evaluator.py           # Matches evidence against criteria, assigns verdicts
│   └── reporter.py            # Generates PDF reports
├── database/
│   ├── db.py                  # SQLite database operations
│   └── schema.sql             # Database table definitions
├── pages/
│   ├── 1_Upload_Documents.py  # Upload tender and bidder files
│   ├── 2_Review_Criteria.py   # Review and edit extracted criteria
│   ├── 3_Evaluation.py        # Run evaluation, view results, override verdicts
│   └── 4_Report.py            # View and export the final report
├── prompts/
│   ├── extract_criteria.txt   # Prompt for extracting criteria from tender
│   ├── classify_document.txt  # Prompt for classifying bidder documents
│   ├── extract_evidence.txt   # Prompt for extracting evidence per criterion
│   └── evaluate_criterion.txt # Prompt for evaluating qualitative criteria
├── sample_data/
│   ├── tender/                # Sample CRPF tender document
│   └── bidders/               # 10 sample bidder submissions (JSON)
└── uploads/                   # Where uploaded files are stored
```

---

## Sample Data Scenarios

The included sample data covers 10 bidders, each testing a different scenario:

| Bidder | Scenario | Expected Verdict |
|--------|----------|-----------------|
| Apex Constructions | Clean pass, all criteria met | Eligible |
| Bharat Builders | Different value formats, scanned docs | Eligible |
| Gupta Infra | Fails turnover, experience, and ISO | Not Eligible |
| Delta Engineering | Was blacklisted within 5-year window | Not Eligible |
| Sharma Construction | Missing 1 of 3 completion certificates | Needs Review |
| Indus Builders | Poor scan quality, borderline turnover | Needs Review |
| Eagle Projects | ISO certificate expired | Needs Review |
| Highland Constructions | "Similar work" is debatable | Needs Review |
| National Infra | Every value exactly at threshold | Eligible |
| Zenith Builders | Entire submission is phone photos | Needs Review |

---

## Switching LLM Models

If `phi3:mini` is too slow or you want to try a different model:

```bash
# Pull a different model
ollama pull llama3.2:3b

# Or if you have 16GB+ RAM:
ollama pull mistral:7b
```

Then edit `config.py` and change:

```python
OLLAMA_MODEL = "llama3.2:3b"  # or "mistral:7b"
```

---

## Troubleshooting

**"Ollama is not running"**
- Open a terminal and run `ollama serve`
- Keep that terminal open while using TenderLens

**"Model not found"**
- Run `ollama pull phi3:mini` to download the model
- Check available models with `ollama list`

**"Tesseract not found"**
- Run `brew install tesseract`
- Verify with `tesseract --version`

**App is very slow**
- The LLM runs locally and is limited by your hardware
- Each evaluation step makes one LLM call — with 10 bidders and 10 criteria, that's ~100 LLM calls
- Try a smaller model: `ollama pull llama3.2:3b` and update config.py
- Close other heavy apps to free up RAM

**Database errors**
- Delete the database file and restart: `rm database/tenderlens.db`
- The database is recreated automatically on startup
