import os
import re
import unicodedata
from difflib import SequenceMatcher

from modules import llm
from config import PROMPTS_DIR


def _load_prompt(name: str) -> str:
    path = os.path.join(PROMPTS_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _normalize_description_for_match(text: str) -> str:
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _merge_criterion_fields(keep: dict, other: dict) -> dict:
    for key in ("expected_evidence", "threshold"):
        a = (keep.get(key) or "").strip()
        b = (other.get(key) or "").strip()
        if len(b) > len(a):
            keep[key] = other.get(key, "")

    a = (keep.get("source_section") or "").strip()
    b = (other.get("source_section") or "").strip()
    if a and b and a != b:
        if a in b:
            keep["source_section"] = b
        elif b not in a:
            keep["source_section"] = f"{a}; {b}"[:500]
    elif len(b) > len(a):
        keep["source_section"] = other.get("source_section", "")
    return keep


def _dedupe_similar_criteria(criteria: list, similarity: float = 0.88) -> list:
    """Drop near-duplicate rows (common when the same mandatory-doc table is repeated twice on a portal)."""
    merged: list[dict] = []
    for c in criteria:
        desc_norm = _normalize_description_for_match(c.get("description", ""))
        if not desc_norm:
            merged.append(dict(c))
            continue
        found = False
        for j, prev in enumerate(merged):
            other_norm = _normalize_description_for_match(prev.get("description", ""))
            if not other_norm:
                continue
            ratio = SequenceMatcher(None, desc_norm, other_norm).ratio()
            if ratio >= similarity or desc_norm in other_norm or other_norm in desc_norm:
                merged[j] = _merge_criterion_fields(dict(prev), c)
                found = True
                break
        if not found:
            merged.append(dict(c))
    for i, c in enumerate(merged):
        c["criterion_id"] = f"C-{i + 1:03d}"
    return merged


def extract_criteria(tender_text: str) -> list:
    """
    Send the full tender text to the LLM and extract structured eligibility criteria.
    Returns a list of criterion dicts.
    """
    prompt_template = _load_prompt("extract_criteria.txt")
    prompt = prompt_template.replace("{tender_text}", tender_text)

    system = (
        "You are a government procurement analyst. "
        "Extract distinct eligibility obligations as structured JSON. "
        "Merge duplicated portal/table listings; omit bid-cover layout rows that repeat structure only. "
        "Do not invent criteria."
    )

    result = llm.chat_json(prompt, system_prompt=system)

    criteria = result.get("criteria", [])
    if isinstance(result, list):
        criteria = result

    cleaned = []
    for i, c in enumerate(criteria):
        cleaned.append({
            "criterion_id": c.get("criterion_id", f"C-{i+1:03d}"),
            "description": c.get("description", ""),
            "category": c.get("category", "other"),
            "mandatory": c.get("mandatory", True),
            "threshold": c.get("threshold", ""),
            "expected_evidence": c.get("expected_evidence", ""),
            "source_section": c.get("source_section", ""),
        })

    cleaned = _dedupe_similar_criteria(cleaned)
    return cleaned
