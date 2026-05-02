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

## Prerequisites

You need three things installed on your Mac before you can run TenderLens. Here are the exact commands:

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
  - Green (✅) = Eligible
  - Red (❌) = Not Eligible
  - Yellow (⚠️) = Needs Manual Review
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

---

## Tech Stack

- **Python 3.11+** — backend language
- **Streamlit** — web UI framework
- **Ollama + Phi-3 Mini** — local LLM (no cloud, no API keys)
- **Tesseract** — OCR for scanned documents and photos
- **PyMuPDF + pdfplumber** — PDF parsing and table extraction
- **SQLite** — local database for audit trail
- **FPDF2** — PDF report generation
