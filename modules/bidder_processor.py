import os
import re
from modules import llm
from modules.ingestion import ingest_document
from config import PROMPTS_DIR


def _load_prompt(name: str) -> str:
    path = os.path.join(PROMPTS_DIR, name)
    with open(path, "r") as f:
        return f.read()


def classify_document(document_text: str) -> dict:
    """Classify a bidder document by its content using the LLM."""
    prompt_template = _load_prompt("classify_document.txt")
    text_snippet = document_text[:2000]
    prompt = prompt_template.replace("{document_text}", text_snippet)

    system = (
        "You are a document classifier for government tender submissions. "
        "Classify the document into exactly one category. Return valid JSON only."
    )

    result = llm.chat_json(prompt, system_prompt=system)
    return {
        "category": result.get("category", "other"),
        "confidence": result.get("confidence", 0.5),
        "reasoning": result.get("reasoning", ""),
    }


def normalize_indian_currency(value_str: str) -> float:
    """Parse Indian currency strings into a numeric value."""
    if not value_str:
        return None

    text = str(value_str).lower().strip()
    text = text.replace("rs.", "").replace("rs", "").replace("inr", "")
    text = text.replace("₹", "").replace("/-", "").strip()

    crore_match = re.search(r"([\d,.]+)\s*(?:crore|cr)", text)
    if crore_match:
        num = crore_match.group(1).replace(",", "")
        try:
            return float(num) * 10_000_000
        except ValueError:
            pass

    lakh_match = re.search(r"([\d,.]+)\s*(?:lakh|lac|l)", text)
    if lakh_match:
        num = lakh_match.group(1).replace(",", "")
        try:
            return float(num) * 100_000
        except ValueError:
            pass

    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    if cleaned:
        try:
            return float(cleaned)
        except ValueError:
            pass

    return None


def extract_evidence_for_criterion(document_text: str, doc_filename: str,
                                    doc_category: str, criterion: dict) -> dict:
    """Ask the LLM to extract evidence for a specific criterion from a document."""
    prompt_template = _load_prompt("extract_evidence.txt")
    prompt = prompt_template.replace("{criterion_description}", criterion.get("description", ""))
    prompt = prompt.replace("{threshold}", criterion.get("threshold", ""))
    prompt = prompt.replace("{doc_category}", doc_category)
    prompt = prompt.replace("{doc_filename}", doc_filename)
    prompt = prompt.replace("{document_text}", document_text[:3000])

    system = (
        "You are extracting specific evidence from a bidder document. "
        "Only extract what is actually present. Do not fabricate values. "
        "Return valid JSON only."
    )

    result = llm.chat_json(prompt, system_prompt=system)
    return {
        "found": result.get("found", False),
        "extracted_value": result.get("extracted_value", ""),
        "normalized_value": result.get("normalized_value"),
        "source_text": result.get("source_text", ""),
        "page_reference": result.get("page_reference"),
        "notes": result.get("notes", ""),
    }


def process_bidder_documents(file_paths: list, criteria: list) -> dict:
    """
    Process all documents for a single bidder:
    1. Ingest each document
    2. Classify each document
    3. Extract evidence per criterion

    Returns a dict with document info and evidence per criterion.
    """
    documents = []
    for fp in file_paths:
        doc_data = ingest_document(fp)
        classification = classify_document(doc_data["full_text"])
        doc_data["doc_category"] = classification["category"]
        doc_data["classification_confidence"] = classification["confidence"]
        doc_data["classification_reasoning"] = classification["reasoning"]
        documents.append(doc_data)

    evidence_map = {}
    for criterion in criteria:
        crit_id = criterion.get("id") or criterion.get("criterion_id")
        evidence_map[crit_id] = []

        for doc in documents:
            result = extract_evidence_for_criterion(
                document_text=doc["full_text"],
                doc_filename=doc["filename"],
                doc_category=doc["doc_category"],
                criterion=criterion,
            )

            if result["found"]:
                result["source_document"] = doc["filename"]
                result["ocr_confidence"] = doc.get("min_ocr_confidence")
                evidence_map[crit_id].append(result)

    return {
        "documents": documents,
        "evidence": evidence_map,
    }
