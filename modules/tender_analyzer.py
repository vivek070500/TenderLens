import os
from modules import llm
from config import PROMPTS_DIR


def _load_prompt(name: str) -> str:
    path = os.path.join(PROMPTS_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def extract_criteria(tender_text: str) -> list:
    """
    Send the full tender text to the LLM and extract structured eligibility criteria.
    Returns a list of criterion dicts.
    """
    prompt_template = _load_prompt("extract_criteria.txt")
    prompt = prompt_template.replace("{tender_text}", tender_text)

    system = (
        "You are a government procurement analyst. "
        "Extract eligibility criteria from tender documents as structured JSON. "
        "Be thorough and precise. Do not invent criteria."
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

    return cleaned
