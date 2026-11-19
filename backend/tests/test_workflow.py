import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_test_database_dir = tempfile.TemporaryDirectory(prefix="brandguard-test-bootstrap-")
os.environ["DATABASE_PATH"] = str(Path(_test_database_dir.name) / "bootstrap.db")

from app import create_app
from database import get_db_connection
from routes.analysis import analyzer
from services.ai_service import AIService
from services.analyzer import GovernanceAnalyzer
from services.rules_engine import RuleBasedAnalyzer


class BrandGuardWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "DATABASE_PATH": str(Path(self.temp_dir.name) / "workflow.db")
        })
        self.client = self.app.test_client()
        analyzer.ai_service.gemini_key = ""

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rule_corrections_replace_only_exact_fixable_spans(self):
        engine = RuleBasedAnalyzer(
            brand={"name": "Northstar", "primary_tone": "professional"},
            facts=[{
                "id": 1,
                "statement": "The organization was established in 2014.",
                "key_metric": "2014",
                "keywords": "organization, established"
            }],
            terminology=[{
                "term": "cheap",
                "replacement": "affordable",
                "active": 1
            }],
            claim_rules=[{
                "pattern_or_trigger": "guarantee outcomes",
                "required_disclaimer": "Add the approved disclaimer.",
                "active": 1
            }]
        )
        source_text = (
            "Cheap tools are cheap. The organization was established in 2024. "
            "We guarantee outcomes."
        )

        result = engine.analyze(source_text)

        self.assertEqual(
            result["corrected_text"],
            "Affordable tools are affordable. The organization was established in 2014. "
            "We guarantee outcomes."
        )
        term_issues = [issue for issue in result["issues"] if issue["category"] == "Terminology"]
        self.assertEqual(len(term_issues), 2)
        self.assertTrue(all(issue["auto_fixable"] for issue in term_issues))
        fact_issue = next(issue for issue in result["issues"] if issue["category"] == "Fact Accuracy")
        self.assertEqual(fact_issue["matched_text"], "2024")
        self.assertEqual(fact_issue["suggestion"], "2014")
        self.assertTrue(fact_issue["auto_fixable"])
        claim_issue = next(issue for issue in result["issues"] if issue["category"] == "Compliance & Claims")
        self.assertFalse(claim_issue["auto_fixable"])
        self.assertNotIn("Add the approved disclaimer", result["corrected_text"])

    def test_empty_database_to_brand_delete_workflow(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        initial_brands = self.client.get("/api/brands").get_json()["brands"]
        self.assertEqual(initial_brands, [])
        self.assertEqual(self.client.get("/api/demo-samples").status_code, 404)

        missing_brand_analysis = self.client.post(
            "/api/analysis/quick-check", json={"text": "No selected brand"}
        )
        self.assertEqual(missing_brand_analysis.status_code, 400)
        self.assertFalse(self.client.post("/api/content", json={"text": "Draft"}).get_json()["success"])

        brand_response = self.client.post("/api/brands", json={
            "name": "Northstar Labs",
            "description": "A brand profile created by this test.",
            "industry": "Research",
            "tagline": "Research for teams",
            "primary_tone": "Clear and factual",
            "voice_traits": ["precise", "approachable"],
            "target_audience": "Research teams"
        })
        self.assertEqual(brand_response.status_code, 200, brand_response.get_json())
        brand_id = brand_response.get_json()["brand_id"]
        brand_details_response = self.client.get(f"/api/brands/{brand_id}")
        self.assertEqual(brand_details_response.status_code, 200)
        self.assertEqual(
            brand_details_response.get_json()["brand"]["description"],
            "A brand profile created by this test."
        )
        listed_brands = self.client.get("/api/brands").get_json()["brands"]
        self.assertTrue(any(brand["id"] == brand_id for brand in listed_brands))
        with self.app.app_context():
            conn = get_db_connection()
            try:
                persisted_brand = conn.execute(
                    "SELECT name, description FROM brands WHERE id = ?", (brand_id,)
                ).fetchone()
                self.assertIsNotNone(persisted_brand)
                self.assertEqual(persisted_brand["name"], "Northstar Labs")
                self.assertEqual(
                    persisted_brand["description"],
                    "A brand profile created by this test."
                )
            finally:
                conn.close()

        update_response = self.client.put(f"/api/brands/{brand_id}", json={
            "primary_tone": "Precise and accessible",
            "voice_traits": ["precise", "accessible", "calm"]
        })
        self.assertTrue(update_response.get_json()["success"])

        fact_response = self.client.post(f"/api/brands/{brand_id}/facts", json={
            "category": "Organization History",
            "statement": "The organization was established in 2014.",
            "key_metric": "2014",
            "keywords": "established, organization history"
        })
        self.assertEqual(fact_response.status_code, 200, fact_response.get_json())
        term_response = self.client.post(f"/api/brands/{brand_id}/terminology", json={
            "term": "best-in-class",
            "replacement": "well-suited",
            "reason": "Use substantiated descriptions.",
            "severity": "warning"
        })
        self.assertEqual(term_response.status_code, 200)
        claim_response = self.client.post(f"/api/brands/{brand_id}/claims", json={
            "rule_name": "No guaranteed outcomes",
            "pattern_or_trigger": "guarantee outcomes",
            "severity": "critical",
            "explanation": "Outcomes must not be guaranteed."
        })
        self.assertEqual(claim_response.status_code, 200, claim_response.get_json())
        unsafe_pattern = self.client.post(f"/api/brands/{brand_id}/claims", json={
            "rule_name": "Unsafe pattern",
            "pattern_or_trigger": "(a+)+$"
        })
        self.assertEqual(unsafe_pattern.status_code, 400)

        visual_response = self.client.put(f"/api/brands/{brand_id}/visual-rules", json={
            "primary_colors": [{"name": "Ocean", "hex": "#126abc"}],
            "secondary_colors": [],
            "forbidden_colors": [],
            "logo_rules": "Maintain clear space.",
            "font_family": "Brand Sans",
            "contrast_minimum": "4.5:1"
        })
        self.assertTrue(visual_response.get_json()["success"])

        first_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Research overview",
            "text": "The organization was established in 2024. We guarantee outcomes."
        })
        self.assertEqual(first_audit.status_code, 200, first_audit.get_json())
        first_data = first_audit.get_json()
        content_id = first_data["content_id"]
        first_result = first_data["result"]
        self.assertEqual(first_data["version"], 1)
        self.assertEqual(first_data["status"], "pending_review")
        self.assertEqual(first_result["risk_level"], "CRITICAL")
        self.assertLessEqual(first_result["score"], 49)
        self.assertTrue(any(issue["severity"] == "CRITICAL" for issue in first_result["issues"]))
        self.assertNotIn("suitable for approval", first_result["summary"].lower())
        self.assertEqual(first_result["engine_used"], "RULE_BASED_FALLBACK")
        self.assertFalse(first_result["visual_validation"]["supported"])
        self.assertTrue(any(issue["category"] == "Fact Accuracy" for issue in first_result["issues"]))

        second_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "content_id": content_id,
            "title": "Research overview",
            "text": "The organization was established in 2014. It has well-suited research tools."
        })
        self.assertEqual(second_audit.status_code, 200, second_audit.get_json())
        self.assertEqual(second_audit.get_json()["version"], 2)

        correction_response = self.client.post(
            f"/api/analysis/apply-corrections/{content_id}",
            json={"corrected_text": "The organization was established in 2014. It has research tools."}
        )
        self.assertEqual(correction_response.status_code, 200, correction_response.get_json())
        self.assertEqual(correction_response.get_json()["new_version"], 3)
        self.assertEqual(correction_response.get_json()["result"]["issues"], [])

        for decision in ("approved", "rejected", "changes_requested"):
            review_response = self.client.post("/api/reviews", json={
                "content_id": content_id,
                "reviewer_name": "Test Reviewer",
                "reviewer_role": "Review Team",
                "decision": decision,
                "comments": f"Recorded {decision} decision."
            })
            self.assertEqual(review_response.status_code, 200, review_response.get_json())
            self.assertEqual(review_response.get_json()["new_status"], decision)

        review_without_name = self.client.post("/api/reviews", json={
            "content_id": content_id, "decision": "approved"
        })
        self.assertEqual(review_without_name.status_code, 400)

        detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(detail["content"]["status"], "changes_requested")
        self.assertEqual(len(detail["versions"]), 3)
        self.assertEqual(len(detail["reviews"]), 3)

        export = self.client.get(f"/api/history/export/{content_id}").get_json()["report"]
        self.assertEqual(export["content_metadata"]["status"], "changes_requested")
        self.assertEqual(len(export["version_history"]), 3)
        self.assertEqual(len(export["review_audit_log"]), 3)

        second_brand = self.client.post("/api/brands", json={"name": "Second Brand"}).get_json()["brand_id"]
        second_content = self.client.post("/api/analysis/audit", json={
            "brand_id": second_brand, "title": "Second brand content", "text": "Text for its own brand."
        })
        self.assertEqual(second_content.status_code, 200)
        scoped_stats = self.client.get(f"/api/history/dashboard-stats?brand_id={brand_id}").get_json()
        other_stats = self.client.get(f"/api/history/dashboard-stats?brand_id={second_brand}").get_json()
        self.assertEqual(scoped_stats["total_scanned"], 1)
        self.assertEqual(other_stats["total_scanned"], 1)
        self.assertEqual(
            self.client.get("/api/history/dashboard-stats?brand_id=1%20OR%201%3D1").status_code,
            400
        )

        with self.app.app_context():
            conn = get_db_connection()
            try:
                conn.execute("DELETE FROM brands WHERE id = ?", (brand_id,))
                conn.commit()
                for table in ("brand_facts", "brand_terminology", "brand_visual_rules",
                              "brand_claim_rules", "content_items", "content_versions",
                              "analysis_results", "reviews"):
                    self.assertEqual(
                        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                        1 if table in (
                            "brand_visual_rules", "content_items", "content_versions", "analysis_results"
                        ) else 0,
                        table
                    )
            finally:
                conn.close()
        delete_response = self.client.delete(f"/api/brands/{second_brand}")
        self.assertTrue(delete_response.get_json()["success"])
        self.assertEqual(self.client.get("/api/brands").get_json()["brands"], [])

    def test_gemini_result_merge_preserves_corrected_text(self):
        scores = {
            "terminology": 100,
            "facts": 100,
            "tone": 100,
            "claims": 100,
            "accessibility": 100
        }
        rule_result = {
            "score": 100,
            "issues": [],
            "sub_scores": scores,
            "readability": {},
            "corrected_text": "Rule-based correction"
        }
        ai_result = {
            "score": 100,
            "issues": [],
            "sub_scores": scores,
            "corrected_text": "Gemini correction"
        }

        result = GovernanceAnalyzer()._merge_results(rule_result, ai_result)

        self.assertEqual(result["corrected_text"], "Rule-based correction")
        self.assertEqual(result["engine_used"], "AI_GEMINI")

    def test_quick_check_returns_ai_violations_and_identifies_the_engine(self):
        brand_id = self.client.post(
            "/api/brands", json={"name": "Test Brand"}
        ).get_json()["brand_id"]
        analyzer.ai_service.gemini_key = "unit-test-key"
        ai_result = {
            "score": 78,
            "sub_scores": {
                "terminology": 100,
                "facts": 100,
                "tone": 100,
                "claims": 74,
                "accessibility": 100
            },
            "issues": [{
                "id": "ai_claim",
                "category": "Compliance & Claims",
                "severity": "WARNING",
                "rule": "Unsupported outcome",
                "matched_text": "guarantees",
                "location": {"start": 12, "end": 22},
                "explanation": "The outcome is not supported by approved facts.",
                "suggestion": "may support"
            }],
            "corrected_text": "Our product may support results.",
            "summary": "A policy warning was detected."
        }

        with patch.object(analyzer.ai_service, "analyze_with_ai", return_value=ai_result):
            response = self.client.post("/api/analysis/quick-check", json={
                "brand_id": brand_id,
                "text": "Our product guarantees results."
            })

        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()["result"]
        self.assertEqual(result["engine_used"], "AI_GEMINI")
        self.assertEqual(result["issues"][0]["severity"], "WARNING")
        self.assertEqual(
            result["issues"][0]["explanation"],
            "The outcome is not supported by approved facts."
        )
        self.assertEqual(result["issues"][0]["suggestion"], "may support")

    def test_gemini_service_builds_backend_request_and_validates_response(self):
        source_text = "This claim needs review."
        ai_response = {
            "score": 82,
            "sub_scores": {
                "terminology": 100,
                "facts": 100,
                "tone": 100,
                "claims": 74,
                "accessibility": 100
            },
            "issues": [{
                "severity": "WARNING",
                "matched_text": "claim",
                "explanation": "Review this claim.",
                "suggestion": "statement"
            }],
            "corrected_text": "This statement needs review.",
            "summary": "A policy warning was detected."
        }
        client = Mock()
        client.models.generate_content.return_value.text = json.dumps(ai_response)
        service = AIService()
        service.gemini_key = "unit-test-key"

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            result = service.analyze_with_ai(
                source_text,
                {"name": "Test Brand"},
                [],
                [],
                [],
                {},
                "Ad Copy",
                "Digital Ads"
            )

        self.assertEqual(result["engine_used"], "AI_GEMINI")
        self.assertEqual(result["corrected_text"], ai_response["corrected_text"])
        self.assertFalse(result["issues"][0]["auto_fixable"])
        client_factory.assert_called_once()
        self.assertEqual(client_factory.call_args.kwargs["api_key"], "unit-test-key")
        self.assertEqual(
            client_factory.call_args.kwargs["http_options"].timeout,
            12_000,
        )
        self.assertEqual(client.models.generate_content.call_args.kwargs["model"], "gemini-3.8-flash")
        self.assertEqual(
            client.models.generate_content.call_args.kwargs["config"].response_mime_type,
            "application/json",
        )


if __name__ == "__main__":
    unittest.main()
