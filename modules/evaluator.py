import re
import os
from modules import llm
from modules.bidder_processor import normalize_indian_currency
from config import (
    PROMPTS_DIR, VERDICT_ELIGIBLE, VERDICT_NOT_ELIGIBLE,
    VERDICT_NEEDS_REVIEW, OCR_CONFIDENCE_MEDIUM, BORDERLINE_THRESHOLD_PERCENT,
)


def _load_prompt(name: str) -> str:
    path = os.path.join(PROMPTS_DIR, name)
    with open(path, "r") as f:
        return f.read()


def _parse_threshold(threshold_str: str) -> dict:
    """Try to parse a threshold string into operator + value."""
    if not threshold_str:
        return None

    match = re.search(r"(>=|<=|>|<|=)\s*([\d,.]+)", threshold_str)
    if match:
        op = match.group(1)
        val = float(match.group(2).replace(",", ""))
        return {"operator": op, "value": val}

    num_match = re.search(r"([\d,.]+)", threshold_str)
    if num_match:
        val = float(num_match.group(1).replace(",", ""))
        return {"operator": ">=", "value": val}

    return None


def _compare(value: float, operator: str, threshold: float) -> bool:
    if operator == ">=":
        return value >= threshold
    elif operator == ">":
        return value > threshold
    elif operator == "<=":
        return value <= threshold
    elif operator == "<":
        return value < threshold
    elif operator == "=":
        return value == threshold
    return False


def _is_borderline(value: float, threshold: float, pct: float) -> bool:
    """Check if value is within pct% of threshold (above or below)."""
    margin = threshold * (pct / 100.0)
    return abs(value - threshold) <= margin


def evaluate_quantitative(criterion: dict, evidence_list: list) -> dict:
    """Evaluate a criterion with a numeric threshold using deterministic rules."""
    threshold_parsed = _parse_threshold(criterion.get("threshold", ""))
    if not threshold_parsed:
        return None

    if not evidence_list:
        return {
            "verdict": VERDICT_NEEDS_REVIEW,
            "explanation": (
                f"No evidence found for: {criterion['description']}. "
                "The required documents may not have been submitted."
            ),
            "confidence": 0.5,
        }

    best_value = None
    best_source = None
    low_ocr = False

    for ev in evidence_list:
        norm = ev.get("normalized_value")
        if norm is None and ev.get("extracted_value"):
            norm = normalize_indian_currency(str(ev["extracted_value"]))

        if norm is not None:
            if best_value is None or norm > best_value:
                best_value = norm
                best_source = ev.get("source_document", "unknown")

        ocr_conf = ev.get("ocr_confidence")
        if ocr_conf is not None and ocr_conf < OCR_CONFIDENCE_MEDIUM:
            low_ocr = True

    if best_value is None:
        return {
            "verdict": VERDICT_NEEDS_REVIEW,
            "explanation": (
                f"Evidence was found for '{criterion['description']}' but no numeric "
                "value could be extracted. Manual verification needed."
            ),
            "confidence": 0.4,
        }

    if low_ocr:
        return {
            "verdict": VERDICT_NEEDS_REVIEW,
            "explanation": (
                f"Extracted value: {best_value:,.0f} from {best_source}. "
                f"However, OCR confidence is low. "
                f"Please manually verify the source document."
            ),
            "confidence": 0.5,
        }

    op = threshold_parsed["operator"]
    thresh = threshold_parsed["value"]

    if _is_borderline(best_value, thresh, BORDERLINE_THRESHOLD_PERCENT):
        passes = _compare(best_value, op, thresh)
        return {
            "verdict": VERDICT_NEEDS_REVIEW if not passes else VERDICT_ELIGIBLE,
            "explanation": (
                f"Extracted value: {best_value:,.0f}. Threshold: {op} {thresh:,.0f}. "
                f"Value is {'at' if best_value == thresh else 'near'} the threshold boundary. "
                f"Source: {best_source}."
            ),
            "confidence": 0.7,
        }

    passes = _compare(best_value, op, thresh)
    if passes:
        return {
            "verdict": VERDICT_ELIGIBLE,
            "explanation": (
                f"Extracted value: {best_value:,.0f} meets threshold {op} {thresh:,.0f}. "
                f"Source: {best_source}."
            ),
            "confidence": 0.95,
        }
    else:
        return {
            "verdict": VERDICT_NOT_ELIGIBLE,
            "explanation": (
                f"Extracted value: {best_value:,.0f} does not meet threshold {op} {thresh:,.0f}. "
                f"Shortfall: {thresh - best_value:,.0f}. Source: {best_source}."
            ),
            "confidence": 0.95,
        }


def evaluate_qualitative(criterion: dict, evidence_list: list) -> dict:
    """Evaluate a qualitative criterion using the LLM."""
    if not evidence_list:
        return {
            "verdict": VERDICT_NEEDS_REVIEW,
            "explanation": (
                f"No evidence found for: {criterion['description']}. "
                "The required documents may not have been submitted."
            ),
            "confidence": 0.5,
        }

    evidence_summary = ""
    for i, ev in enumerate(evidence_list, 1):
        evidence_summary += (
            f"\n{i}. Document: {ev.get('source_document', 'unknown')}\n"
            f"   Extracted: {ev.get('extracted_value', 'N/A')}\n"
            f"   Detail: {ev.get('source_text', 'N/A')}\n"
            f"   Notes: {ev.get('notes', 'None')}\n"
        )

    prompt_template = _load_prompt("evaluate_criterion.txt")
    prompt = prompt_template.replace("{criterion_description}", criterion.get("description", ""))
    prompt = prompt.replace("{category}", criterion.get("category", ""))
    prompt = prompt.replace("{mandatory}", str(criterion.get("mandatory", True)))
    prompt = prompt.replace("{threshold}", criterion.get("threshold", "N/A"))
    prompt = prompt.replace("{evidence_summary}", evidence_summary)

    system = (
        "You are evaluating a bidder against a tender criterion. "
        "Be fair and conservative. When in doubt, choose NEEDS_REVIEW. "
        "Return valid JSON only."
    )

    result = llm.chat_json(prompt, system_prompt=system)

    verdict = result.get("verdict", VERDICT_NEEDS_REVIEW)
    if verdict not in (VERDICT_ELIGIBLE, VERDICT_NOT_ELIGIBLE, VERDICT_NEEDS_REVIEW):
        verdict = VERDICT_NEEDS_REVIEW

    return {
        "verdict": verdict,
        "explanation": result.get("explanation", "No explanation provided."),
        "confidence": result.get("confidence", 0.5),
    }


def evaluate_criterion(criterion: dict, evidence_list: list) -> dict:
    """
    Main evaluation entry point for a single criterion.
    Routes to quantitative or qualitative evaluation.
    """
    category = criterion.get("category", "")
    threshold = criterion.get("threshold", "")

    has_numeric_threshold = bool(re.search(r"\d", threshold)) if threshold else False

    if has_numeric_threshold and category in ("financial", "experience"):
        quant_result = evaluate_quantitative(criterion, evidence_list)
        if quant_result:
            return quant_result

    return evaluate_qualitative(criterion, evidence_list)


def evaluate_bidder(criteria: list, evidence_map: dict) -> list:
    """
    Evaluate a single bidder against all criteria.
    Returns a list of verdict dicts.
    """
    results = []
    for criterion in criteria:
        crit_id = criterion.get("id") or criterion.get("criterion_id")
        evidence_list = evidence_map.get(crit_id, [])
        verdict = evaluate_criterion(criterion, evidence_list)
        verdict["criterion_id"] = crit_id
        verdict["criterion_description"] = criterion.get("description", "")
        verdict["category"] = criterion.get("category", "")
        verdict["mandatory"] = criterion.get("mandatory", True)
        results.append(verdict)

    return results


def compute_overall_verdict(verdict_list: list) -> str:
    """Determine the overall verdict for a bidder from individual criterion verdicts."""
    mandatory_verdicts = [v for v in verdict_list if v.get("mandatory", True)]

    if any(v["verdict"] == VERDICT_NOT_ELIGIBLE for v in mandatory_verdicts):
        return VERDICT_NOT_ELIGIBLE

    if any(v["verdict"] == VERDICT_NEEDS_REVIEW for v in mandatory_verdicts):
        return VERDICT_NEEDS_REVIEW

    return VERDICT_ELIGIBLE
