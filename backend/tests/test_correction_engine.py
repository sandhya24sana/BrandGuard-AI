import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_test_database_dir = tempfile.TemporaryDirectory(prefix="brandguard-test-bootstrap-")
os.environ["DATABASE_PATH"] = str(Path(_test_database_dir.name) / "bootstrap.db")

from app import create_app
from database import get_db_connection
from routes.analysis import analyzer
from services.correction_engine import apply_corrections, validate_replacement_text
from services.rules_engine import RuleBasedAnalyzer


class CorrectionEngineTests(unittest.TestCase):
    def test_phrase_replacements_handle_case_and_preserve_punctuation(self):
        examples = (
            ("cures diabetes!", "supports wellness", "supports wellness!"),
            ("CURES DIABETES!", "supports wellness", "SUPPORTS WELLNESS!"),
            ("Cures Diabetes.", "supports wellness", "Supports Wellness."),
        )
        for source, replacement, expected in examples:
            with self.subTest(source=source):
                result = RuleBasedAnalyzer(
                    brand={"name": "Test"},
                    facts=[],
                    terminology=[{
                        "id": 3,
                        "term": "cures diabetes",
                        "replacement_text": replacement,
                        "replacement": replacement,
                        "active": 1,
                    }],
                ).analyze(source)
                self.assertEqual(result["corrected_text"], expected)
                self.assertEqual(len(result["issues"]), 1)
                self.assertEqual(result["issues"][0]["matched_text"], source[:-1])

    def test_repeated_matches_and_similar_words_are_handled_by_exact_spans(self):
        source = "Cheaply, cheap! CHEAP.\ncheapener is not cheap."
        result = RuleBasedAnalyzer(
            brand={"name": "Test"},
            facts=[],
            terminology=[{
                "id": 7,
                "term": "cheap",
                "replacement_text": "affordable",
                "replacement": "affordable",
                "active": 1,
            }],
        ).analyze(source)

        self.assertEqual(
            result["corrected_text"],
            "Cheaply, affordable! AFFORDABLE.\ncheapener is not affordable.",
        )
        self.assertEqual(len(result["issues"]), 3)

    def test_long_multparagraph_text_preserves_unrelated_content(self):
        source = (
            "Header: cheap, affordable options.\n\n"
            + ("Unrelated detail with punctuation; preserve every line. " * 120)
            + "\n\nClosing: cheap."
        )
        result = RuleBasedAnalyzer(
            brand={"name": "Test"},
            facts=[],
            terminology=[{
                "id": 8,
                "term": "cheap",
                "replacement_text": "affordable",
                "replacement": "affordable",
                "active": 1,
            }],
        ).analyze(source)

        self.assertEqual(len(result["issues"]), 2)
        self.assertTrue(result["corrected_text"].startswith("Header: affordable, affordable options."))
        self.assertTrue(result["corrected_text"].endswith("\n\nClosing: affordable."))
        self.assertIn("Unrelated detail with punctuation; preserve every line.", result["corrected_text"])

    def test_disclaimers_are_not_replacements_and_dynamic_rules_remain_fixable(self):
        source = "Our product guarantees weight loss. It cures diabetes."
        result = RuleBasedAnalyzer(
            brand={"name": "New Brand"},
            facts=[],
            terminology=[{
                "id": 1,
                "term": "cures diabetes",
                "replacement_text": "supports healthier choices",
                "replacement": "supports healthier choices",
            }],
            claim_rules=[{
                "id": 9,
                "rule_name": "Health outcome claims",
                "pattern_or_trigger": r"\bguarantees weight loss\b",
                "required_disclaimer": "Claims require substantiation.",
                "explanation": "Guaranteed health outcomes are unsupported.",
            }],
        ).analyze(source)

        term_issue = next(issue for issue in result["issues"] if issue["category"] == "Terminology")
        claim_issue = next(issue for issue in result["issues"] if issue["category"] == "Compliance & Claims")
        self.assertEqual(result["corrected_text"], "Our product guarantees weight loss. It supports healthier choices.")
        self.assertTrue(term_issue["auto_fixable"])
        self.assertEqual(term_issue["replacement_text"], "supports healthier choices")
        self.assertFalse(claim_issue["auto_fixable"])
        self.assertIsNone(claim_issue["replacement_text"])
        self.assertEqual(claim_issue["required_disclaimer"], "Claims require substantiation.")
        self.assertNotIn("Claims require substantiation.", result["corrected_text"])
        self.assertLessEqual(result["score"], 49)

    def test_explicit_legacy_quoted_claim_alternative_is_a_valid_replacement(self):
        source = "guarantees weight loss"
        disclaimer = 'Avoid medical claims. Use: "Enjoy food made with natural ingredients."'
        result = RuleBasedAnalyzer(
            brand={"name": "Existing Brand"},
            facts=[],
            terminology=[],
            claim_rules=[{
                "id": 14,
                "rule_name": "Unsupported Medical Claims",
                "pattern_or_trigger": r"\bguarantees weight loss\b",
                "required_disclaimer": disclaimer,
                "explanation": "Guaranteed outcomes require substantiation.",
            }],
        ).analyze(source)

        issue = result["issues"][0]
        self.assertTrue(issue["auto_fixable"])
        self.assertEqual(issue["replacement_text"], "Enjoy food made with natural ingredients.")
        self.assertEqual(issue["required_disclaimer"], disclaimer)
        self.assertEqual(result["corrected_text"], "Enjoy food made with natural ingredients.")

    def test_missing_ambiguous_and_instruction_replacements_require_manual_review(self):
        source = "cheap claims; fast claims; unsafe claims"
        result = RuleBasedAnalyzer(
            brand={"name": "Test"},
            facts=[],
            terminology=[
                {"id": 1, "term": "cheap", "replacement_text": None, "replacement": ""},
                {"id": 2, "term": "fast", "replacement": "quick / rapid"},
                {"id": 3, "term": "unsafe", "replacement_text": "Use: safe wording"},
            ],
        ).analyze(source)

        self.assertEqual(result["corrected_text"], source)
        self.assertTrue(all(not issue["auto_fixable"] for issue in result["issues"]))
        self.assertTrue(all(issue["manual_review_required"] for issue in result["issues"]))
        with self.assertRaises(ValueError):
            validate_replacement_text("Avoid the claim")
        with self.assertRaises(ValueError):
            validate_replacement_text("cheap", "Cheap")

    def test_multiple_corrections_apply_once_and_overlap_fails_closed(self):
        source = "alpha beta alpha"
        first = {
            "id": "first",
            "matched_text": "alpha",
            "replacement_text": "one",
            "auto_fixable": True,
            "location": {"start": 0, "end": 5},
        }
        second_start = source.rindex("alpha")
        second = {
            **first,
            "id": "second",
            "location": {"start": second_start, "end": second_start + 5},
        }
        bulk = apply_corrections(source, [first, second])
        reversed_selection = apply_corrections(
            source, [first, second], ["second", "first"]
        )
        self.assertEqual(bulk["text"], "one beta one")
        self.assertEqual(reversed_selection["text"], bulk["text"])
        self.assertCountEqual(bulk["applied_issue_ids"], ["first", "second"])

        overlapping = apply_corrections("abcdef", [
            {
                "id": "left",
                "matched_text": "abcd",
                "replacement_text": "left",
                "auto_fixable": True,
                "location": {"start": 0, "end": 4},
            },
            {
                "id": "right",
                "matched_text": "cdef",
                "replacement_text": "right",
                "auto_fixable": True,
                "location": {"start": 2, "end": 6},
            },
        ])
        self.assertEqual(overlapping["text"], "abcdef")
        self.assertEqual(set(overlapping["manual_review"]), {"left", "right"})

    def test_duplicate_same_span_is_replaced_once_and_stale_apply_is_rejected(self):
        issue = {
            "id": "same",
            "matched_text": "bad",
            "replacement_text": "good",
            "auto_fixable": True,
            "location": {"start": 0, "end": 3},
        }
        duplicate = {**issue, "id": "duplicate"}
        result = apply_corrections("bad text", [issue, duplicate])
        self.assertEqual(result["text"], "good text")
        self.assertCountEqual(result["applied_issue_ids"], ["same", "duplicate"])

        repeated = apply_corrections(result["text"], [issue], ["same"])
        self.assertEqual(repeated["text"], result["text"])
        self.assertIn("same", repeated["manual_review"])


class CorrectionWorkflowAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "corrections.db"
        self.app = create_app({
            "TESTING": True,
            "DATABASE_PATH": str(self.db_path),
        })
        self.client = self.app.test_client()
        analyzer.ai_service.gemini_key = ""
        brand_response = self.client.post("/api/brands", json={"name": "Reusable Brand"})
        self.brand_id = brand_response.get_json()["brand_id"]
        self.client.post(f"/api/brands/{self.brand_id}/terminology", json={
            "term": "cheap",
            "replacement_text": "affordable",
            "severity": "warning",
        })
        self.client.post(f"/api/brands/{self.brand_id}/claims", json={
            "rule_name": "No guaranteed outcomes",
            "pattern_or_trigger": r"\bguarantees weight loss\b",
            "replacement_text": "may support healthy habits",
            "required_disclaimer": "Substantiate health claims.",
            "explanation": "Do not claim guaranteed health outcomes.",
            "severity": "critical",
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_individual_then_adopt_all_reanalyzes_and_preserves_every_version(self):
        original = (
            "Cheap plans are advertised as 'guarantees weight loss'; "
            "cheap snacks repeat 'guarantees weight loss'."
        )
        audit = self.client.post("/api/analysis/audit", json={
            "brand_id": self.brand_id,
            "title": "Correction test",
            "text": original,
        }).get_json()
        content_id = audit["content_id"]
        term_issues = [
            issue for issue in audit["result"]["issues"]
            if issue["category"] == "Terminology"
        ]
        self.assertEqual(len(term_issues), 2)

        individual = self.client.post(
            f"/api/analysis/apply-corrections/{content_id}",
            json={"issue_ids": [term_issues[0]["id"]]},
        )
        self.assertEqual(individual.status_code, 200, individual.get_json())
        individual_data = individual.get_json()
        once_corrected = (
            "Affordable plans are advertised as 'guarantees weight loss'; "
            "cheap snacks repeat 'guarantees weight loss'."
        )
        self.assertEqual(individual_data["applied_text"], once_corrected)
        self.assertEqual(individual_data["new_version"], 2)
        self.assertEqual(individual_data["result"]["score"], 49)
        self.assertTrue(individual_data["correction_review"]["manual_review_required"])
        self.assertFalse(individual_data["correction_review"]["grammar_validated"])
        self.assertTrue(individual_data["result"]["issues"])

        repeated = self.client.post(
            f"/api/analysis/apply-corrections/{content_id}",
            json={"issue_ids": [term_issues[0]["id"]]},
        )
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(repeated.get_json()["success"], False)

        adopted = self.client.post(
            f"/api/analysis/apply-corrections/{content_id}",
            json={"apply_all": True},
        )
        self.assertEqual(adopted.status_code, 200, adopted.get_json())
        adopted_data = adopted.get_json()
        expected = (
            "Affordable plans are advertised as 'may support healthy habits'; "
            "affordable snacks repeat 'may support healthy habits'."
        )
        self.assertEqual(adopted_data["applied_text"], expected)
        self.assertEqual(adopted_data["new_version"], 3)
        self.assertEqual(adopted_data["result"]["issues"], [])
        self.assertEqual(adopted_data["result"]["score"], 100)

        detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual([version["version_number"] for version in detail["versions"]], [3, 2, 1])
        self.assertEqual(detail["versions"][-1]["text"], original)
        self.assertEqual(detail["versions"][1]["text"], once_corrected)
        self.assertEqual(detail["versions"][0]["text"], expected)
        self.assertEqual(detail["content"]["risk_score"], adopted_data["result"]["score"])

    def test_claim_disclaimer_without_replacement_cannot_be_applied(self):
        response = self.client.post(f"/api/brands/{self.brand_id}/claims", json={
            "rule_name": "Manual-only health claim",
            "pattern_or_trigger": r"\bcures diabetes\b",
            "required_disclaimer": "Use only medically supported statements.",
            "explanation": "This claim requires substantiation.",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        audit = self.client.post("/api/analysis/audit", json={
            "brand_id": self.brand_id,
            "title": "Manual correction",
            "text": "The cheap treatment cures diabetes.",
        }).get_json()
        claim = next(
            issue for issue in audit["result"]["issues"]
            if issue["rule"] == "Manual-only health claim"
        )
        self.assertFalse(claim["auto_fixable"])
        self.assertEqual(claim["suggestion"], "")
        self.assertEqual(claim["required_disclaimer"], "Use only medically supported statements.")
        applied = self.client.post(
            f"/api/analysis/apply-corrections/{audit['content_id']}",
            json={"issue_ids": [claim["id"]]},
        )
        self.assertEqual(applied.status_code, 409)

        bulk = self.client.post(
            f"/api/analysis/apply-corrections/{audit['content_id']}",
            json={"apply_all": True},
        )
        self.assertEqual(bulk.status_code, 200, bulk.get_json())
        self.assertEqual(bulk.get_json()["applied_text"], "The affordable treatment cures diabetes.")
        self.assertTrue(bulk.get_json()["correction_review"]["manual_review_required"])
        self.assertEqual(len(bulk.get_json()["result"]["issues"]), 1)
        self.assertLessEqual(bulk.get_json()["result"]["score"], 49)

        no_safe_corrections = self.client.post(
            f"/api/analysis/apply-corrections/{audit['content_id']}",
            json={"apply_all": True},
        )
        self.assertEqual(no_safe_corrections.status_code, 409)
        self.assertEqual(
            self.client.get(f"/api/content/{audit['content_id']}").get_json()["content"]["current_version"],
            2,
        )

    def test_legacy_claim_alternative_uses_individual_apply_and_central_engine(self):
        brand_id = self.client.post(
            "/api/brands",
            json={"name": "Legacy Claim Brand"},
        ).get_json()["brand_id"]
        disclaimer = 'Avoid medical claims. Use: "Enjoy food made with natural ingredients."'
        claim_response = self.client.post(f"/api/brands/{brand_id}/claims", json={
            "rule_name": "Unsupported Medical Claims",
            "pattern_or_trigger": r"\bguarantees weight loss\b",
            "required_disclaimer": disclaimer,
            "explanation": "Guaranteed health outcomes are unsupported.",
        })
        self.assertEqual(claim_response.status_code, 200, claim_response.get_json())

        audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Legacy claim",
            "text": "guarantees weight loss",
        }).get_json()
        issue = next(
            issue for issue in audit["result"]["issues"]
            if issue["rule"] == "Unsupported Medical Claims"
        )
        self.assertTrue(issue["auto_fixable"])
        self.assertEqual(issue["replacement_text"], "Enjoy food made with natural ingredients.")
        self.assertEqual(issue["required_disclaimer"], disclaimer)

        response = self.client.post(
            f"/api/analysis/apply-corrections/{audit['content_id']}",
            json={"issue_ids": [issue["id"]]},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            response.get_json()["applied_text"],
            "Enjoy food made with natural ingredients.",
        )
        self.assertEqual(response.get_json()["result"]["issues"], [])

    def test_rule_creation_validates_replacement_text_and_keeps_disclaimer_legacy(self):
        invalid = self.client.post(f"/api/brands/{self.brand_id}/claims", json={
            "rule_name": "Invalid replacement",
            "pattern_or_trigger": "claim",
            "replacement_text": "Use: an approved safe phrase",
            "required_disclaimer": "Existing API field remains supported.",
        })
        self.assertEqual(invalid.status_code, 400)
        valid = self.client.post(f"/api/brands/{self.brand_id}/claims", json={
            "rule_name": "Legacy manual rule",
            "pattern_or_trigger": "old claim",
            "required_disclaimer": "This disclaimer remains guidance only.",
        })
        self.assertEqual(valid.status_code, 200)


class AdditiveGuidelineMigrationTests(unittest.TestCase):
    def test_existing_rule_data_is_preserved_when_replacement_columns_are_added(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.db"
            connection = sqlite3.connect(db_path)
            connection.executescript("""
                CREATE TABLE brand_terminology (
                    id INTEGER PRIMARY KEY,
                    brand_id INTEGER NOT NULL,
                    term TEXT NOT NULL,
                    replacement TEXT NOT NULL,
                    reason TEXT,
                    category TEXT DEFAULT 'Terminology',
                    severity TEXT DEFAULT 'warning',
                    active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE brand_claim_rules (
                    id INTEGER PRIMARY KEY,
                    brand_id INTEGER NOT NULL,
                    rule_name TEXT NOT NULL,
                    pattern_or_trigger TEXT NOT NULL,
                    severity TEXT DEFAULT 'critical',
                    required_disclaimer TEXT,
                    explanation TEXT,
                    active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE reviews (
                    id INTEGER PRIMARY KEY,
                    content_id INTEGER NOT NULL,
                    reviewer_name TEXT NOT NULL,
                    reviewer_role TEXT,
                    decision TEXT NOT NULL,
                    comments TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO brand_terminology (id, brand_id, term, replacement)
                    VALUES (1, 1, 'cheap', 'affordable');
                INSERT INTO brand_terminology (id, brand_id, term, replacement)
                    VALUES (2, 1, 'fast', 'quick / rapid');
                INSERT INTO brand_claim_rules (
                    id, brand_id, rule_name, pattern_or_trigger, required_disclaimer
                ) VALUES (1, 1, 'Legacy claim', 'guaranteed', 'Review the claim.');
                INSERT INTO reviews (
                    id, content_id, reviewer_name, decision, comments
                ) VALUES (1, 99, 'Legacy reviewer', 'approved', 'Keep this history.');
            """)
            connection.commit()
            connection.close()

            app = create_app({
                "TESTING": True,
                "DATABASE_PATH": str(db_path),
            })
            with app.app_context():
                connection = get_db_connection()
                try:
                    rows = connection.execute(
                        "SELECT id, replacement, replacement_text FROM brand_terminology ORDER BY id"
                    ).fetchall()
                    claim = connection.execute(
                        "SELECT required_disclaimer, replacement_text FROM brand_claim_rules WHERE id = 1"
                    ).fetchone()
                    review = connection.execute(
                        "SELECT content_version, reviewer_name, comments FROM reviews WHERE id = 1"
                    ).fetchone()
                finally:
                    connection.close()

            self.assertEqual(rows[0]["replacement"], "affordable")
            self.assertEqual(rows[0]["replacement_text"], "affordable")
            self.assertEqual(rows[1]["replacement"], "quick / rapid")
            self.assertIsNone(rows[1]["replacement_text"])
            self.assertEqual(claim["required_disclaimer"], "Review the claim.")
            self.assertIsNone(claim["replacement_text"])
            self.assertIsNone(review["content_version"])
            self.assertEqual(review["reviewer_name"], "Legacy reviewer")
            self.assertEqual(review["comments"], "Keep this history.")


if __name__ == "__main__":
    unittest.main()
