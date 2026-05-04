import re
import os
from modules import llm
from modules.bidder_processor import (
    normalize_indian_currency, extract_currency_from_text, parse_count_requirement,
    currency_amount_from_evidence,
)
from config import (
    PROMPTS_DIR, VERDICT_ELIGIBLE, VERDICT_NOT_ELIGIBLE,
    VERDICT_NEEDS_REVIEW, OCR_CONFIDENCE_MEDIUM, BORDERLINE_THRESHOLD_PERCENT,
)


def _load_prompt(name: str) -> str:
    path = os.path.join(PROMPTS_DIR, name)
    with open(path, "r") as f:
        return f.read()


def _years_threshold_from_description(description: str) -> dict:
    """If the criterion is clearly about years of experience, return a year threshold.

    Parsed before any currency heuristics so values like \"minimum 10 years\" are not
    mis-read as rupee amounts by ``parse_money`` / LLM disambiguation.
    """
    dlow = (description or "").lower()
    if not (("year" in dlow or "yrs" in dlow) and (
        "experience" in dlow or "engineer" in dlow or "personnel" in dlow
    )):
        return None
    patterns = [
        r"(?:at\s*least|minimum|min\.?)\s+(\d+)\s*(?:years?|yrs?)(?:\s+of\s+experience)?",
        r"(\d+)\s*(?:years?|yrs?)\s+of\s+experience",
        r"with\s+(?:at\s*least\s+)?(?:minimum|min\.?)\s+(\d+)\s*(?:years?|yrs?)",
    ]
    for p in patterns:
        m = re.search(p, dlow)
        if m:
            return {"operator": ">=", "value": float(m.group(1))}
    return None


def _criterion_is_qualitative_only(description: str, threshold_str: str) -> bool:
    """True when the criterion is about presence of soft merit / standards — not ₹ / counts.

    Stops bogus numeric thresholds (e.g. copied ``>= 10000000``) from firing on ISO 14001
    (where \"14001\" is the standard number) or on \"ahead of schedule\" / desirable wording.
    """
    dlow = (description or "").lower()
    if any(
        x in dlow
        for x in (
            "rs.", "rupees", "inr", "crore", "lakh", "turnover",
            "net worth", "earnest money", " emd", "similar work",
            "completed at least", "value not less",
        )
    ):
        return False

    # Before generic currency parse — "ISO 14001" is often mis-read as a rupee amount.
    if re.search(r"\biso\s*\d{4}\b", dlow):
        return True

    merit_markers = (
        "desirable", "favourably", "favorably", "weightage", "weight-age",
        "additional marks", "bonus", "preferred", "carry additional",
        "viewed favourably", "viewed favorably",
    )
    if any(m in dlow for m in merit_markers):
        return True

    if "ahead of schedule" in dlow:
        return True
    if "track record" in dlow and ("project" in dlow or "complet" in dlow):
        return True

    if extract_currency_from_text(description):
        return False

    return False


def _parse_threshold(criterion: dict) -> dict:
    """Parse a numeric threshold from a criterion.

    Strategy (in order of authority):
      - Skip entirely (return None) for qualitative merit / ISO-presence wording so a
        bogus numeric *threshold* field does not apply.
      - **Years of experience** in personnel criteria (before generic currency parse).
      - Rupee amounts and counts in description, then threshold field (existing logic).

    Returns {"operator": ">=" or "<=" etc, "value": float} or None.
    """
    description = criterion.get("description", "")
    threshold_str = criterion.get("threshold", "")

    op_match = re.search(r"(>=|<=|>|<|=)", str(threshold_str))
    operator = op_match.group(1) if op_match else ">="

    if _criterion_is_qualitative_only(description, threshold_str):
        return None

    yt = _years_threshold_from_description(description)
    if yt is not None:
        if op_match:
            yt["operator"] = operator
        return yt

    desc_currency = extract_currency_from_text(description)
    if desc_currency is not None and desc_currency > 0:
        return {"operator": operator, "value": desc_currency}

    years_match = re.search(
        r"(?:at\s*least|minimum|min\.?)\s*(\d+)\s*(?:years?|yrs?)",
        description.lower(),
    )
    if years_match:
        return {"operator": ">=", "value": float(years_match.group(1))}

    plain_years = re.search(r"(\d+)\s*(?:years?|yrs?)\s+(?:of\s+)?experience",
                            description.lower())
    if plain_years:
        return {"operator": ">=", "value": float(plain_years.group(1))}

    thresh_currency = extract_currency_from_text(threshold_str)
    if thresh_currency is not None and thresh_currency > 0:
        return {"operator": operator, "value": thresh_currency}

    if not threshold_str:
        return None

    match = re.search(r"(>=|<=|>|<|=)\s*([\d,.]+)", str(threshold_str))
    if match:
        op = match.group(1)
        try:
            val = float(match.group(2).replace(",", ""))
            return {"operator": op, "value": val}
        except ValueError:
            return None

    num_match = re.search(r"([\d,.]+)", str(threshold_str))
    if num_match:
        try:
            val = float(num_match.group(1).replace(",", ""))
            return {"operator": ">=", "value": val}
        except ValueError:
            return None

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


def _no_evidence_verdict(criterion: dict) -> dict:
    """Return the appropriate verdict when no evidence was found.

    For OPTIONAL (desirable) criteria: ELIGIBLE — bidder simply didn't claim the bonus.
    For MANDATORY criteria: NEEDS_REVIEW — flag for officer attention.
    """
    if not criterion.get("mandatory", True):
        return {
            "verdict": VERDICT_ELIGIBLE,
            "explanation": (
                f"Optional criterion not claimed: {criterion.get('description', '')[:120]}. "
                "Does not affect overall eligibility."
            ),
            "confidence": 1.0,
        }
    return {
        "verdict": VERDICT_NEEDS_REVIEW,
        "explanation": (
            f"No evidence found for: {criterion.get('description', '')}. "
            "The required documents may not have been submitted."
        ),
        "confidence": 0.5,
    }


def _to_numeric(value) -> float:
    """Best-effort conversion to a numeric value.

    Handles currency (with Indian Crore/Lakh suffixes), bare numbers
    ("10 years" -> 10.0), and explicit numeric inputs. Returns None when
    no number can be extracted.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    s = str(value).strip()
    if not s:
        return None

    cur = normalize_indian_currency(s)
    if cur is not None and cur > 0:
        return cur

    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


RUPEE_THRESHOLD_FLOOR = 50_000.0
"""Threshold values below this are treated as years / counts / small ints, not rupees."""


def _use_rupee_evidence_parsing(threshold_parsed: dict, criterion: dict) -> bool:
    """Use currency extraction on evidence only when the criterion compares rupee-scale amounts."""
    if not threshold_parsed:
        return False
    v = threshold_parsed.get("value")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return False
    if v < RUPEE_THRESHOLD_FLOOR:
        return False
    d = (criterion.get("description") or "").lower()
    if "year" in d and "experience" in d and v <= 80:
        return False
    return True


def _normalised_evidence_values(
    evidence_list: list,
    criterion: dict,
    threshold_parsed: dict,
) -> list:
    """Return a list of (numeric_value, source, evidence_dict) tuples."""
    description = criterion.get("description", "")
    required_count = parse_count_requirement(description)
    try:
        thresh_val = float(threshold_parsed.get("value", 0))
    except (TypeError, ValueError):
        thresh_val = 0.0

    rupee_first = _use_rupee_evidence_parsing(threshold_parsed, criterion)
    rupee_only = rupee_first and required_count > 1 and thresh_val >= 100_000

    out = []
    for ev in evidence_list:
        norm = None
        if rupee_first:
            norm = currency_amount_from_evidence(ev)
        if norm is None and not rupee_only:
            norm = _to_numeric(ev.get("extracted_value"))
        if norm is None and not rupee_only:
            norm = _to_numeric(ev.get("source_text"))
        if norm is None and not rupee_only:
            norm = _to_numeric(ev.get("normalized_value"))
        if norm is not None:
            out.append((norm, ev.get("source_document", "unknown"), ev))
    return out


def evaluate_quantitative(criterion: dict, evidence_list: list) -> dict:
    """Evaluate a criterion with a numeric threshold using deterministic rules."""
    threshold_parsed = _parse_threshold(criterion)
    if not threshold_parsed:
        return None

    if not evidence_list:
        return _no_evidence_verdict(criterion)

    op = threshold_parsed["operator"]
    thresh = threshold_parsed["value"]
    description = criterion.get("description", "")
    required_count = parse_count_requirement(description)

    values = _normalised_evidence_values(evidence_list, criterion, threshold_parsed)

    low_ocr = any(
        (ev.get("ocr_confidence") is not None and
         ev.get("ocr_confidence") < OCR_CONFIDENCE_MEDIUM)
        for ev in evidence_list
    )

    if not values:
        return {
            "verdict": VERDICT_NEEDS_REVIEW,
            "explanation": (
                f"Evidence was found for '{description[:100]}' but no numeric "
                "value could be extracted. Manual verification needed."
            ),
            "confidence": 0.4,
        }

    # ── "At least N items each ≥ X" semantic ──
    if required_count > 1:
        qualifying = [(v, src) for v, src, _ in values if _compare(v, op, thresh)]
        actual = len(qualifying)
        if actual >= required_count:
            sources = ", ".join(src for _, src in qualifying[:required_count])
            return {
                "verdict": VERDICT_ELIGIBLE,
                "explanation": (
                    f"Found {actual} qualifying items meeting threshold {op} {thresh:,.0f} "
                    f"(required: {required_count}). Sources: {sources}."
                ),
                "confidence": 0.95,
            }
        return {
            "verdict": VERDICT_NOT_ELIGIBLE,
            "explanation": (
                f"Only {actual} out of {required_count} required items meet threshold "
                f"{op} {thresh:,.0f}. Values found: "
                + ", ".join(f"{v:,.0f}" for v, _, _ in values) + "."
            ),
            "confidence": 0.95,
        }

    # ── Single-value semantic (default) ──
    best_value, best_source, _ = max(values, key=lambda t: t[0])

    if low_ocr:
        return {
            "verdict": VERDICT_NEEDS_REVIEW,
            "explanation": (
                f"Extracted value: {best_value:,.0f} from {best_source}. "
                f"However, OCR confidence is low. Please manually verify."
            ),
            "confidence": 0.5,
        }

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
        return _no_evidence_verdict(criterion)

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
        "Be fair and DECISIVE. When clear evidence supports the criterion, choose ELIGIBLE. "
        "Use NEEDS_REVIEW only for genuinely missing, contradictory, or borderline evidence. "
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
    description = criterion.get("description", "")
    threshold = criterion.get("threshold", "")
    category = criterion.get("category", "")

    # Try quantitative path if there is ANY recognisable numeric content,
    # either in the threshold field or the description (currency / years / count).
    has_numeric = bool(re.search(r"\d", str(threshold))) or bool(
        extract_currency_from_text(description)
    ) or bool(re.search(r"\d+\s*(?:years?|yrs?)", description.lower()))

    if has_numeric and category in ("financial", "experience", "technical"):
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
