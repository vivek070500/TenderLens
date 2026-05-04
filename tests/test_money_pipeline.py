"""Unit tests for hybrid money normalization (rules + optional LLM off)."""
import os
import sys
import unittest

# Repo root on path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ["TENDERLENS_MONEY_LLM"] = "0"

from modules.money_pipeline import (  # noqa: E402
    comma_grouped_indian_or_western,
    parse_money,
)


class TestCommaGrouping(unittest.TestCase):
    def test_indian_lakh_grouping(self):
        self.assertEqual(comma_grouped_indian_or_western("10,00,000"), 1_000_000)

    def test_western_millions_grouping(self):
        self.assertEqual(comma_grouped_indian_or_western("10,000,000"), 10_000_000)


class TestParseMoneyRulesOnly(unittest.TestCase):
    """LLM disabled via env — exercises rule stages only."""

    def test_inr_emd_ten_lakh(self):
        r = parse_money(
            "EMD Rs. 10,00,000/- (Rupees Ten Lakhs only)",
            use_llm=False,
        )
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "INR")
        self.assertAlmostEqual(r.amount, 1_000_000)

    def test_inr_two_crore_comma(self):
        r = parse_money("similar works each Rs. 2,00,00,000 (Two Crore)", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "INR")
        self.assertAlmostEqual(r.amount, 20_000_000)

    def test_inr_word_crore(self):
        r = parse_money("average turnover five crore rupees", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "INR")
        self.assertAlmostEqual(r.amount, 50_000_000)

    def test_usd_million(self):
        r = parse_money("Performance bond USD 2.5 million", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "USD")
        self.assertAlmostEqual(r.amount, 2_500_000)

    def test_usd_preprocessed(self):
        r = parse_money("Bank draft $1,234.56 for fees", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "USD")
        self.assertAlmostEqual(r.amount, 1234.56)

    def test_eur_frenchish_decimal(self):
        r = parse_money("Equipment: 22,90 EUR", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "EUR")
        self.assertAlmostEqual(r.amount, 22.90)

    def test_no_false_price_on_experience_years(self):
        r = parse_money("minimum 10 years of civil engineering experience", use_llm=False)
        self.assertIsNone(r)

    def test_german_decimal_comma_eur(self):
        r = parse_money("Quoted at 1.234,56 EUR net", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "EUR")
        self.assertAlmostEqual(r.amount, 1234.56)

    def test_inr_scale_before_crore_reverse_order(self):
        r = parse_money("Turnover aggregated 5.25 crores INR", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "INR")
        self.assertAlmostEqual(r.amount, 52_500_000)

    def test_million_usd_trailing_iso(self):
        r = parse_money("Bond for 3.2 million USD", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "USD")
        self.assertAlmostEqual(r.amount, 3_200_000)

    def test_fy_year_range_filtered(self):
        r = parse_money("Tax audit for F.Y. 2023-24 assessment", use_llm=False)
        self.assertIsNone(r)

    def test_percent_line_not_money(self):
        r = parse_money("Performance score 95% in internal QA", use_llm=False)
        self.assertIsNone(r)

    def test_space_thousands(self):
        r = parse_money("Equipment EUR 1 234 567.89 all inclusive", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "EUR")
        self.assertAlmostEqual(r.amount, 1_234_567.89)

    def test_thirteen_crore_words(self):
        r = parse_money("Turnover not less than thirteen crore rupees", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "INR")
        self.assertAlmostEqual(r.amount, 130_000_000)

    def test_crores_plural(self):
        r = parse_money("Each project above 2 crores value", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "INR")
        self.assertAlmostEqual(r.amount, 20_000_000)

    def test_parentheses_accounting_negative_usd(self):
        r = parse_money("Adjustment (USD 500.00) per schedule", use_llm=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.currency, "USD")
        self.assertAlmostEqual(r.amount, 500.0)


if __name__ == "__main__":
    unittest.main()
