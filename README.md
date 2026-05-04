# TenderLens

**Local, AI-assisted eligibility checks for procurement**

TenderLens helps you turn a tender and bidder files into a **clear pass / fail / needs review** summary—with short explanations and a **PDF you can file or share**. Everything runs on **your computer**: your documents stay local (no cloud API required if you use [Ollama](https://ollama.com)).

---

## Run the project after cloning (step by step)

Use these steps the first time and whenever you set up a new machine.

### Part A — Install the tools (one time per computer)

1. **Python 3.11 or newer**  
   - Check: `python --version` (Windows) or `python3 --version` (Mac/Linux).  
   - Install from [python.org](https://www.python.org/downloads/) if needed (Windows: tick **Add Python to PATH**).

2. **Ollama** (runs the AI on your machine)  
   - Download: [ollama.com/download](https://ollama.com/download)  
   - After install, open a terminal and pull the **chat** model and the **embedding** model (used for optional smart document search):
     ```bash
     ollama pull llama3.2:3b
     ollama pull nomic-embed-text
     ```
   - **Keep Ollama running** while you use TenderLens (Mac/Linux: `ollama serve`; on Windows it often runs in the background).

3. **Tesseract OCR** (only if you use **scanned PDFs or photos** of documents)  
   - **Windows (PowerShell):**  
     `winget install --id UB-Mannheim.TesseractOCR --accept-source-agreements --accept-package-agreements`  
   - **Mac:** `brew install tesseract`  
   - Check: `tesseract --version` (restart the terminal after install).

### Part B — Set up TenderLens (every clone)

1. **Open a terminal** and go to the project folder:
   ```bash
   cd TenderLens
   ```

2. **Create a virtual environment** (keeps dependencies isolated):
   ```bash
   # Windows (PowerShell)
   python -m venv venv
   .\venv\Scripts\Activate.ps1

   # Mac / Linux
   python3 -m venv venv
   source venv/bin/activate
   ```
   If Windows blocks the activate script, run once:  
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

3. **Install Python packages:**
   ```bash
   pip install -r requirements.txt
   ```

### Part C — Start the app

1. Make sure **Ollama is running** and you have pulled **`llama3.2:3b`** (see Part A).
2. With the **virtual environment activated** and terminal **inside the project folder**:
   ```bash
   streamlit run app.py
   ```
3. Your browser should open **http://localhost:8501** — that is TenderLens.

---

## Using TenderLens (simple workflow)

| Step | What you do | What TenderLens does |
|------|-------------|----------------------|
| **1 · Intake** | Upload the **tender** and each **bidder’s** documents. | Reads PDFs, Word files, text, and images (with OCR when needed). Pulls out **eligibility rules** from the tender text. |
| **2 · Criteria** | Read and adjust the rules (wording, thresholds, mandatory/optional). | Stores the agreed checklist for every bidder. |
| **3 · Evaluation** | Start the run. | For each bidder and each rule, collects **evidence** from their files, scores **Eligible / Not eligible / Needs review**, and explains why. |
| **4 · Report** | Review the summary; **Build PDF** then **Download PDF**. | Produces a **formatted report** (cover page, summary table, per-bidder detail, officer overrides if any). |

**Verdicts (plain language)**  
- **Eligible** — evidence clearly meets the rule.  
- **Not eligible** — evidence clearly does not meet a **mandatory** rule (or the overall outcome is failed).  
- **Needs review** — something is missing, unclear, or borderline; a human should decide.

---

## How the system fits together (non-technical view)

Think of four connected boxes:

1. **Document desk** — All uploads are turned into searchable text (including scans via OCR).  
2. **Rule book** — The tender is distilled into a checklist officers can edit.  
3. **Evidence and scoring** — For each bidder, the tool finds the best excerpts per rule, normalises numbers (e.g. rupees), uses **deterministic checks** where possible, and uses the **local AI** where judgment is needed.  
4. **Report** — Results are shown on screen and can be **exported as a PDF**.

Optional **vector search** (Chroma + embeddings) helps match the right passages to each rule when wording differs; you can turn behaviour on or off with environment variables (see below).

---

## Tech stack (short)

| Piece | Role |
|--------|------|
| **Streamlit** | Web interface (`app.py` + pages under `pages/`). |
| **SQLite** | Stores tenders, criteria, bidders, evidence, verdicts (`database/`). |
| **Ollama** | Local LLM and embeddings (`config.py` defaults: `llama3.2:3b`, `nomic-embed-text`). |
| **PyMuPDF / pdfplumber / python-docx / Pillow + Tesseract** | Reading PDFs, tables, Word, and images. |
| **fpdf2** | PDF export (`modules/reporter.py`). |

---

## Project layout (for developers)

```
TenderLens/
├── app.py                 # Home / navigation hub
├── config.py              # Models, paths, feature flags
├── requirements.txt
├── .streamlit/config.toml # UI theme
├── pages/
│   ├── 1_Upload_Documents.py
│   ├── 2_Review_Criteria.py
│   ├── 3_Evaluation.py
│   └── 4_Report.py
├── modules/
│   ├── llm.py             # Ollama chat / JSON
│   ├── ingestion.py       # File → text + metadata
│   ├── ocr.py             # Tesseract pipeline
│   ├── tender_analyzer.py # Criteria extraction
│   ├── bidder_processor.py# Chunks, retrieval, evidence extraction
│   ├── evaluator.py       # Verdicts (rules + LLM)
│   ├── reporter.py        # PDF report
│   ├── rag_index.py       # Optional Chroma index
│   ├── money_pipeline.py# Numeric / currency parsing helpers
│   ├── session_workspace.py
│   └── ui_theme.py
├── prompts/               # LLM prompt templates (*.txt)
├── test_upload_data/      # Example tender + bidder fixtures (optional local tests)
├── database/schema.sql
└── tests/                 # Unit tests (pytest)
```

**Prompt files in use:** `extract_criteria.txt`, `extract_evidence.txt`, `evaluate_criterion.txt`.

---

## Configuration highlights (`config.py` & environment)

| Variable | Meaning |
|----------|--------|
| `TENDERLENS_PERSIST=1` | Keep tenders and results on disk across browser sessions; default is session-style reset. |
| `TENDERLENS_RAG=0` | Turn off vector indexing/retrieval extras. |
| `TENDERLENS_EVIDENCE_RAG=1` | Prefer vector retrieval for evidence chunks (default is sequential document scan). |

Edit `config.py` to change the **chat model** name (default `llama3.2:3b`) or file paths.

---

## Troubleshooting

| Symptom | What to try |
|---------|-------------|
| **Ollama / model errors** | Run `ollama list`, pull `llama3.2:3b` and `nomic-embed-text`, ensure Ollama is running. |
| **Tesseract not found** | Install Tesseract for your OS, restart terminal and Streamlit, verify `tesseract --version`. |
| **Windows: cannot run `Activate.ps1`** | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| **Stale or corrupt database** | Stop the app, delete `database/tenderlens.db`, restart (schema is recreated). |

---

## Tests (optional)

```bash
pip install pytest
pytest tests/
```

---

## Disclaimer

TenderLens supports **structured preliminary review**. **Procurement decisions remain with the responsible authority.** Verify critical outcomes independently.
