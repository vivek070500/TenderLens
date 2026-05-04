"""Hybrid money normalization: culture-aware rules + optional LLM (Ollama).

Design follows common "regex + ML" financial extraction practice: high-precision
rules first; optional JSON-mode LLM when confidence is low or candidates disagree.

Stages:
  1) Indian subcontinent: Rs/₹/INR + lakh/crore (singular/plural) + word multiples
  2) Reverse-order scales: "5.2 crores INR"
  3) European decimal comma (1.234,56 EUR) and similar
  4) English scales: million/billion + ISO or $
  5) price-parser for additional Western/EU retail strings
  6) Noise rejection (FY years, percentages, experience-only text)
  7) Optional LLM disambiguation

Output is major units (rupees, dollars, …), not paise/cents unless clearly marked.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    from price_parser import Price
except ImportError:
    Price = None


def _money_llm_enabled() -> bool:
    return os.environ.get("TENDERLENS_MONEY_LLM", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


_LLM_CONF_THRESHOLD = float(os.environ.get("TENDERLENS_MONEY_LLM_MIN_RULE_CONF", "0.62"))


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "twenty-five": 25, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}

_ENGLISH_SCALE = {
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}

_CUR_NORMALIZE = {
    None: None,
    "": None,
    "rs": "INR", "rs.": "INR", "inr": "INR", "₹": "INR", "rupees": "INR", "rupee": "INR",
    "usd": "USD", "us$": "USD", "us dollar": "USD", "$": "USD",
    "eur": "EUR", "€": "EUR",
    "gbp": "GBP", "£": "GBP",
    "jpy": "JPY", "yen": "JPY", "¥": "JPY",
    "aud": "AUD", "cad": "CAD", "chf": "CHF", "cny": "CNY", "rmb": "CNY",
    "sgd": "SGD", "aed": "AED", "sar": "SAR",
    "hkd": "HKD", "nzd": "NZD", "krw": "KRW", "zar": "ZAR", "sek": "SEK",
    "nok": "NOK", "dkk": "DKK", "pln": "PLN", "mxn": "MXN", "brl": "BRL",
    "try": "TRY", "ils": "ILS",
}


@dataclass
class MoneyParse:
    amount: float
    currency: str
    confidence: float
    method: str

    def as_inr_float(self) -> Optional[float]:
        if self.currency == "INR":
            return self.amount
        return None


def comma_grouped_indian_or_western(num_str: str) -> float:
    """10,00,000 → 1e6 (Indian); 10,000,000 → 1e7 (Western)."""
    if not num_str:
        raise ValueError
    num_str = str(num_str).strip().replace("\u00a0", "").replace("\u202f", "")
    num_str = num_str.replace(" ", "")
    m = re.match(r"^([\d,]+)(\.\d+)?$", num_str)
    if not m:
        raise ValueError
    intpart, frac = m.group(1), m.group(2) or ""
    parts = intpart.split(",")
    if len(parts) == 1:
        return float(parts[0] + frac)
    if len(parts[-1]) != 3:
        return float(intpart.replace(",", "") + frac)
    middle = parts[1:-1]
    if 1 <= len(parts[0]) <= 3 and all(len(x) == 2 for x in middle):
        return float("".join(parts) + frac)
    if 1 <= len(parts[0]) <= 3 and all(len(x) == 3 for x in middle):
        return float("".join(parts) + frac)
    return float(intpart.replace(",", "") + frac)


def _parse_european_decimal(num: str) -> Optional[float]:
    """German/Dutch style: 1.234,56 → 1234.56 ; 12.345,67."""
    s = num.strip().replace(" ", "").replace("\u00a0", "")
    if "," not in s:
        return None
    parts = s.split(",")
    dec = parts[-1]
    if not dec.isdigit() or len(dec) > 2:
        return None
    whole = ",".join(parts[:-1]).replace(".", "")
    try:
        return float(f"{whole}.{dec}")
    except ValueError:
        return None


def _split_signed_number_token(raw: str) -> Tuple[str, int]:
    """Strip leading negatives / accounting parentheses."""
    s = raw.strip()
    sign = 1
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
        sign = -1
    if s.startswith("-"):
        sign *= -1
        s = s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    return s, sign


def _norm_cur(tag: Optional[str]) -> Optional[str]:
    if tag is None:
        return None
    t = str(tag).strip().lower().rstrip(".")
    if t in _CUR_NORMALIZE:
        return _CUR_NORMALIZE[t] or None
    if len(t) == 3 and t.isalpha():
        return t.upper()
    return None


def _preprocess_for_price_parser(text: str) -> str:
    s = re.sub(
        r"\$\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?)",
        r"USD \1",
        text,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"USD\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?)\b",
        r"USD \1",
        s,
        flags=re.IGNORECASE,
    )
    return s


def _rules_indian(text: str) -> List[MoneyParse]:
    if not text:
        return []
    t = str(text).lower()
    out: List[MoneyParse] = []

    def add(amt: float, method: str, conf: float = 0.93, sign: int = 1):
        amt = amt * sign
        if amt and abs(amt) > 0:
            out.append(MoneyParse(abs(amt), "INR", conf, method))

    cro_pat = r"(?:crore|crores|cr)\b"
    lak_pat = r"(?:lakh|lakhs|lac)\b"

    for pat, mult, label in (
        (rf"rs\.?\s*([\d][\d,.]*)\s*{cro_pat}", 10_000_000, "inr_digit_cr"),
        (rf"rs\.?\s*([\d][\d,.]*)\s*{lak_pat}", 100_000, "inr_digit_lc"),
        (rf"₹\s*([\d][\d,.]*)\s*{cro_pat}", 10_000_000, "inr_sym_cr"),
        (rf"₹\s*([\d][\d,.]*)\s*{lak_pat}", 100_000, "inr_sym_lc"),
        (rf"inr\s*([\d][\d,.]*)\s*{cro_pat}", 10_000_000, "inr_iso_cr"),
        (rf"inr\s*([\d][\d,.]*)\s*{lak_pat}", 100_000, "inr_iso_lc"),
    ):
        m = re.search(pat, t, re.I)
        if m:
            raw, sgn = _split_signed_number_token(m.group(1).replace(",", ""))
            try:
                add(float(raw) * mult, label, sign=sgn)
            except ValueError:
                pass

    # "5.2 crores" / "5.2 crore INR" (number before scale)
    m = re.search(
        rf"([\d][\d,.]*(?:\.\d+)?)\s*{cro_pat}\s*(?:inr|rupees?|₹)?",
        t,
        re.I,
    )
    if m:
        raw, sgn = _split_signed_number_token(m.group(1))
        try:
            add(float(raw.replace(",", "")) * 10_000_000, "inr_scale_before_cr", 0.9, sgn)
        except ValueError:
            pass
    m = re.search(
        rf"([\d][\d,.]*(?:\.\d+)?)\s*{lak_pat}\s*(?:inr|rupees?|₹)?",
        t,
        re.I,
    )
    if m:
        raw, sgn = _split_signed_number_token(m.group(1))
        try:
            add(float(raw.replace(",", "")) * 100_000, "inr_scale_before_lc", 0.89, sgn)
        except ValueError:
            pass

    word_pat_cr = (
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
        r"twenty|twenty-five|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\s+"
        rf"{cro_pat}"
    )
    word_pat_lc = (
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
        r"twenty|twenty-five|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\s+"
        rf"{lak_pat}"
    )
    for pat, mult in ((word_pat_cr, 10_000_000), (word_pat_lc, 100_000)):
        m = re.search(pat, t)
        if m and m.group(1) in _NUMBER_WORDS:
            add(float(_NUMBER_WORDS[m.group(1)]) * mult, "inr_word_scale")

    for pat, mult, label in (
        (rf"([\d][\d,]*(?:\.\d+)?)\s*{cro_pat}", 10_000_000, "bare_cr"),
        (rf"([\d][\d,]*(?:\.\d+)?)\s*{lak_pat}", 100_000, "bare_lc"),
    ):
        m = re.search(pat, t)
        if m:
            raw, sgn = _split_signed_number_token(m.group(1))
            try:
                add(float(raw.replace(",", "")) * mult, label, 0.88, sgn)
            except ValueError:
                pass

    m = re.search(r"rs\.?\s*([\d][\d,]+(?:\.\d+)?)\b", t)
    if m:
        try:
            raw, sgn = _split_signed_number_token(m.group(1))
            if "," in raw:
                add(comma_grouped_indian_or_western(raw), "inr_rs_comma", 0.9, sgn)
            else:
                add(float(raw.replace(",", "")), "inr_rs_plain", 0.85, sgn)
        except (ValueError, TypeError):
            pass

    m = re.search(r"₹\s*([\d][\d,]+(?:\.\d+)?)\b", t)
    if m:
        try:
            raw, sgn = _split_signed_number_token(m.group(1))
            if "," in raw:
                add(comma_grouped_indian_or_western(raw), "inr_sym_comma", 0.9, sgn)
            else:
                add(float(raw.replace(",", "")), "inr_sym_plain", 0.85, sgn)
        except (ValueError, TypeError):
            pass

    if any(w in t for w in ("lakh", "lac", "crore", "rupee", "inr", "rs.", "rs ", "₹")):
        m = re.search(r"\b([\d]{1,3}(?:,\d{2})*,\d{3}(?:\.\d+)?)\b", text)
        if m:
            try:
                add(comma_grouped_indian_or_western(m.group(1)), "inr_grouped_ctx", 0.82)
            except (ValueError, TypeError):
                pass

    return out


def _rules_european_decimal(text: str) -> List[MoneyParse]:
    out: List[MoneyParse] = []
    for m in re.finditer(
        r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\s*(EUR|€|USD|US\$|US\s*\$|GBP|£|CHF|INR)?\b",
        text,
        re.I,
    ):
        amt = _parse_european_decimal(m.group(1))
        if amt is None or amt <= 0:
            continue
        tag = (m.group(2) or "").upper()
        if "EUR" in tag or "€" in m.group(0):
            iso = "EUR"
        elif "USD" in tag or "US" in tag or "$" in m.group(0):
            iso = "USD"
        elif "GBP" in tag or "£" in m.group(0):
            iso = "GBP"
        elif "CHF" in tag:
            iso = "CHF"
        elif "INR" in tag:
            iso = "INR"
        else:
            iso = "EUR"
        out.append(MoneyParse(amt, iso, 0.84, "eu_decimal"))
    return out


def _rules_english_scale(text: str) -> List[MoneyParse]:
    if not text:
        return []
    t = text
    out: List[MoneyParse] = []
    iso_list = (
        r"USD|EUR|GBP|INR|AUD|CAD|CHF|CNY|SGD|JPY|HKD|NZD|ZAR|SEK|NOK|DKK|"
        r"PLN|MXN|BRL|TRY|ILS|KRW|AED|SAR"
    )
    rx = re.compile(
        rf"({iso_list})\s*"
        r"([\d,]+(?:\.\d+)?)\s*"
        r"(thousand|million|billion|trillion)\b",
        re.I,
    )
    for m in rx.finditer(t):
        iso = m.group(1).upper()
        try:
            base = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        scale = _ENGLISH_SCALE.get(m.group(3).lower(), 1)
        amt = base * scale
        if amt > 0:
            out.append(MoneyParse(amt, iso, 0.86, f"eng_scale_{iso.lower()}"))

    rx_rev = re.compile(
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(thousand|million|billion|trillion)\b",
        re.I,
    )
    for m in rx_rev.finditer(t):
        try:
            base = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        scale = _ENGLISH_SCALE.get(m.group(2).lower(), 1)
        out.append(MoneyParse(base * scale, "USD", 0.84, "usd_scale"))

    # Trailing ISO: 2.5 million USD
    rx_trail = re.compile(
        rf"([\d,]+(?:\.\d+)?)\s*(thousand|million|billion|trillion)\s*({iso_list})\b",
        re.I,
    )
    for m in rx_trail.finditer(t):
        try:
            base = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        iso = m.group(3).upper()
        scale = _ENGLISH_SCALE.get(m.group(2).lower(), 1)
        amt = base * scale
        if amt > 0:
            out.append(MoneyParse(amt, iso, 0.85, f"eng_scale_trail_{iso.lower()}"))

    return out


def _rules_space_thousands_western(text: str) -> List[MoneyParse]:
    """e.g. EUR 1 234 567.89"""
    out: List[MoneyParse] = []
    for m in re.finditer(
        r"\b(USD|EUR|GBP|CHF|INR|AUD|CAD)\s+"
        r"(\d{1,3}(?:\s\d{3})+(?:\.\d+)?)\b",
        text,
        re.I,
    ):
        iso = m.group(1).upper()
        try:
            amt = float(m.group(2).replace(" ", ""))
        except ValueError:
            continue
        if amt > 0:
            out.append(MoneyParse(amt, iso, 0.8, "space_thousands"))
    return out


def _rules_price_parser(text: str) -> List[MoneyParse]:
    if not Price:
        return []
    raw = _preprocess_for_price_parser(str(text))
    out: List[MoneyParse] = []
    seen = set()
    for seg in {raw, raw.replace("₹", "Rs. ")}:
        _try_price_string(seg.strip(), out, seen, whole=True)
    for part in re.split(r"[\n;|]", raw):
        part = part.strip()
        if len(part) < 3 or not re.search(r"\d", part):
            continue
        _try_price_string(part, out, seen, whole=False)
    return out


def _try_price_string(part: str, out: List[MoneyParse], seen: set, whole: bool) -> None:
    try:
        p = Price.fromstring(part)
        if p.amount is None:
            return
        amt = float(p.amount)
        if amt <= 0:
            return
        cur = _norm_cur(p.currency) or _infer_currency_from_text(part)
        key = (round(amt, 6), cur)
        if key in seen:
            return
        seen.add(key)
        conf = 0.72 if cur else 0.55
        label = "price_parser" if whole else "price_parser_seg"
        out.append(MoneyParse(amt, cur or "XXX", conf, label))
    except Exception:
        pass


def _is_probably_experience_years_only(s: str) -> bool:
    t = str(s).lower()
    if "year" not in t and "yrs" not in t:
        return False
    money_cues = (
        "rs.", " rs", "rupee", "inr", "₹", "lakh", "lac", "crore",
        "emd", "turnover", "worth", "deposit", "guarantee", "tender",
        "fee", "cost", "price", "amount", "value",
        "usd", "eur", "gbp", "$", "€", "£",
    )
    return not any(c in t for c in money_cues)


def _infer_currency_from_text(s: str) -> Optional[str]:
    u = s.upper()
    if "INR" in u or "RUPEE" in u or "RS." in u or " RS " in u.strip().upper() or "₹" in s:
        return "INR"
    if re.search(r"\bUSD\b|\bUS\$\b", u) or ("$" in s and not re.search(r"\d+%", s)):
        return "USD"
    if "EUR" in u or "€" in s:
        return "EUR"
    if "GBP" in u or "£" in s:
        return "GBP"
    if "CHF" in u:
        return "CHF"
    if "JPY" in u or "¥" in s:
        return "JPY"
    return None


def _is_ambiguous(cands: List[MoneyParse]) -> bool:
    if len(cands) < 2:
        return False
    top = sorted(cands, key=lambda c: c.confidence, reverse=True)[:5]
    curs = {c.currency for c in top if c.currency != "XXX"}
    if len(curs) >= 2:
        return True
    amts = sorted({round(c.amount, 4) for c in top})
    if len(amts) >= 2 and amts[-1] / max(amts[0], 1e-9) < 5:
        return True
    return False


def _is_noise_candidate(c: MoneyParse, original_text: str) -> bool:
    """Drop likely non-money hits (FY years, bare percentages, etc.)."""
    t = original_text.lower()
    amt = c.amount
    if c.method.startswith("price_parser"):
        if 1980 <= amt <= 2100 and float(amt).is_integer():
            if re.search(r"\bf\.?y\.?\s*\d{4}", t) or "financial year" in t:
                return True
            if re.search(r"\d{4}\s*[-–]\s*\d{2,4}", original_text):
                return True
    if "%" in original_text and 0 < amt <= 100 and float(amt).is_integer():
        if re.search(r"\d+(?:\.\d+)?\s*%", original_text):
            if "rs" not in t and "inr" not in t and "$" not in original_text:
                return True
    return False


def _post_filter_candidates(cands: List[MoneyParse], text: str) -> List[MoneyParse]:
    return [c for c in cands if not _is_noise_candidate(c, text)]


def _dedupe_amounts(cands: List[MoneyParse]) -> List[MoneyParse]:
    """Keep highest confidence per (currency, rounded amount)."""
    best: dict = {}
    for c in cands:
        key = (c.currency, round(c.amount, 4))
        if key not in best or c.confidence > best[key].confidence:
            best[key] = c
    return list(best.values())


def _llm_money(text: str) -> Optional[MoneyParse]:
    if not text or not _money_llm_enabled():
        return None
    try:
        from modules import llm
    except ImportError:
        return None
    snippet = str(text).strip()[:900]
    prompt = (
        "Extract the PRIMARY monetary amount from this fragment.\n"
        "Indian: 10,00,000 = 1000000 INR (ten lakh). 1 crore = 10000000 INR.\n"
        "European: 1.234,56 = 1234.56 when comma is decimal separator.\n"
        "US/UK: 1,234.56 = 1234.56.\n"
        'Return JSON only: {"amount": <number or null>, "currency": <ISO4217 or null>, '
        '"confidence": 0.0-1.0}\n\n---\n'
        f"{snippet}\n---"
    )
    try:
        if not getattr(llm, "is_available", lambda: True)():
            return None
        raw = llm.chat_json(
            prompt,
            system_prompt="You output valid JSON only. Major units only (not cents/paise).",
            fast=True,
            num_predict=256,
        )
        amt = raw.get("amount")
        cur = raw.get("currency")
        conf = float(raw.get("confidence") or 0.5)
        if amt is None:
            return None
        amt = float(amt)
        if amt <= 0:
            return None
        cur = str(cur or "INR").upper()
        if len(cur) != 3:
            cur = _norm_cur(cur) or "INR"
        return MoneyParse(amt, cur, min(0.9, max(0.4, conf)), "llm_disambiguate")
    except Exception:
        return None


def _pick_best(
    cands: List[MoneyParse],
    default_currency: str,
    prefer_currency: Optional[str],
) -> Optional[MoneyParse]:
    if not cands:
        return None
    pref = prefer_currency or default_currency

    def sort_key(c: MoneyParse):
        boost = 0.08 if c.currency == pref else 0.0
        if c.currency == "XXX" and pref == "INR":
            boost = 0.05
        largeness = 0.0
        if c.currency == pref and c.amount >= 1_000_000:
            largeness = 0.02
        return (c.confidence + boost + largeness, c.amount)

    best = sorted(cands, key=sort_key, reverse=True)[0]
    if best.currency == "XXX":
        best = MoneyParse(best.amount, default_currency, best.confidence * 0.92, best.method + "+default_cur")
    return best


def parse_money(
    text,
    *,
    default_currency: str = "INR",
    prefer_currency: str = "INR",
    use_llm: Optional[bool] = None,
) -> Optional[MoneyParse]:
    """Parse the best single monetary amount and ISO currency from free text."""
    if not text or not str(text).strip():
        return None
    use_llm_flag = _money_llm_enabled() if use_llm is None else bool(use_llm)

    cands: List[MoneyParse] = []
    cands.extend(_rules_indian(text))
    cands.extend(_rules_european_decimal(text))
    cands.extend(_rules_english_scale(text))
    cands.extend(_rules_space_thousands_western(text))
    cands.extend(_rules_price_parser(text))

    if _is_probably_experience_years_only(text):
        cands = [c for c in cands if not c.method.startswith("price_parser")]

    cands = _post_filter_candidates(cands, text)
    cands = _dedupe_amounts(cands)

    best_conf = max((c.confidence for c in cands), default=0.0)
    ambiguous = _is_ambiguous(cands)

    if use_llm_flag and (best_conf < _LLM_CONF_THRESHOLD or ambiguous or not cands):
        llm_c = _llm_money(text)
        if llm_c:
            cands.append(llm_c)

    fixed: List[MoneyParse] = []
    for c in cands:
        if c.currency in (None, "XXX"):
            fixed.append(MoneyParse(c.amount, default_currency, c.confidence * 0.9, c.method))
        else:
            fixed.append(c)

    return _pick_best(_dedupe_amounts(fixed), default_currency, prefer_currency)
