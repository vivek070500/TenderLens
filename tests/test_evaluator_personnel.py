"""Personnel / years thresholds must not be mis-parsed as rupee amounts."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("TENDERLENS_MONEY_LLM", "0")

from modules.evaluator import (  # noqa: E402
    _parse_threshold,
    evaluate_quantitative,
)


class TestPersonnelYearsThreshold(unittest.TestCase):
    def test_years_win_over_misleading_threshold_field(self):
        crit = {
            "description": (
                "The bidder must have adequate technical personnel on their rolls, "
                "including at least one qualified civil engineer with minimum 10 years "
                "of experience."
            ),
            "threshold": ">= 10000000",
            "category": "technical",
        }
        parsed = _parse_threshold(crit)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["value"], 10.0)

        ev = [
            {
                "extracted_value": "18",
                "source_text": "18 years in civil construction",
                "source_document": "13_technical_personnel.txt",
                "ocr_confidence": 95.0,
            }
        ]
        r = evaluate_quantitative(crit, ev)
        self.assertEqual(r["verdict"], "ELIGIBLE")


class TestQualitativeMeritCriteria(unittest.TestCase):
    """ISO / desirable / schedule wording must not use bogus ₹ thresholds."""

    def test_iso_14001_desirable_no_numeric_parse(self):
        crit = {
            "description": (
                "Possession of ISO 14001:2015 (Environmental Management) certification "
                "is desirable and will carry additional weightage of up to 5 marks."
            ),
            "threshold": ">= 10000000",
            "category": "experience",
        }
        self.assertIsNone(_parse_threshold(crit))
        ev = [
            {
                "extracted_value": "ISO 14001:2015",
                "source_text": "Certificate of Registration for Environmental Management System",
                "source_document": "17_iso_14001_certificate.txt",
                "ocr_confidence": 95.0,
            }
        ]
        # Would have been NOT_ELIGIBLE if quantitative compared 14001 to 10M
        r = evaluate_quantitative(crit, ev)
        self.assertIsNone(r)

    def test_ahead_of_schedule_no_numeric_parse(self):
        crit = {
            "description": (
                "Track record of completing projects ahead of schedule will be "
                "viewed favourably."
            ),
            "threshold": ">= 10000000",
            "category": "experience",
        }
        self.assertIsNone(_parse_threshold(crit))

    def test_similar_works_still_parsed(self):
        crit = {
            "description": (
                "The bidder must have completed at least three (3) similar works, "
                "each of value not less than Rs. 2,00,00,000/- (Rupees Two Crore only)."
            ),
            "threshold": ">= 20000000",
            "category": "experience",
        }
        p = _parse_threshold(crit)
        self.assertIsNotNone(p)
        self.assertGreaterEqual(p["value"], 2_000_000)


if __name__ == "__main__":
    unittest.main()
