import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_test_database_dir = tempfile.TemporaryDirectory(prefix="brandguard-test-bootstrap-")
os.environ["DATABASE_PATH"] = str(Path(_test_database_dir.name) / "bootstrap.db")

from app import create_app
from routes.analysis import analyzer
from services.ai_service import AIService


class BackendReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "DATABASE_PATH": str(Path(self.temp_dir.name) / "reliability.db"),
        })
        self.client = self.app.test_client()
        self.original_gemini_key = analyzer.ai_service.gemini_key
        analyzer.ai_service.gemini_key = ""

    def tearDown(self):
        analyzer.ai_service.gemini_key = self.original_gemini_key
        self.temp_dir.cleanup()

    def create_brand(self, name="Reliability Brand"):
        response = self.client.post("/api/brands", json={"name": name})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["brand_id"]

    def test_invalid_request_shapes_and_field_types_return_client_errors(self):
        self.assertEqual(self.client.post("/api/brands", json=[]).status_code, 400)
        malformed_json = self.client.post(
            "/api/brands",
            data="{",
            content_type="application/json",
        )
        self.assertEqual(malformed_json.status_code, 400, malformed_json.get_json())
        brand_id = self.create_brand()

        invalid_requests = (
            ("/api/analysis/quick-check", {
                "brand_id": True,
                "text": "valid text",
            }),
            ("/api/analysis/audit", {
                "brand_id": brand_id,
                "text": "valid text",
                "channel": [],
            }),
            (f"/api/brands/{brand_id}/facts", {
                "statement": [],
            }),
            (f"/api/brands/{brand_id}/claims", []),
            ("/api/content", {
                "brand_id": brand_id,
                "title": [],
                "text": "valid text",
            }),
        )
        for path, payload in invalid_requests:
            with self.subTest(path=path):
                response = self.client.post(path, json=payload)
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertFalse(response.get_json()["success"])

    def test_ai_timeout_fails_over_without_raising_or_replacing_rule_results(self):
        service = AIService()
        service.gemini_key = "unit-test-key"
        client = Mock()
        client.models.generate_content.side_effect = ValueError("provider timed out")
        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            result = service.analyze_with_ai(
                "Sample content",
                {"name": "Reliability Brand"},
                [],
                [],
                [],
                {},
                "Ad Copy",
                "Digital Ads",
            )
        self.assertIsNone(result)

    def test_audit_and_draft_preserve_source_whitespace_and_offsets(self):
        brand_id = self.create_brand()
        term_response = self.client.post(
            f"/api/brands/{brand_id}/terminology",
            json={"term": "cheap", "replacement_text": "affordable"},
        )
        self.assertEqual(term_response.status_code, 200, term_response.get_json())

        source_text = " \ncheap \t"
        quick_check = self.client.post("/api/analysis/quick-check", json={
            "brand_id": brand_id,
            "text": source_text,
        })
        self.assertEqual(quick_check.status_code, 200, quick_check.get_json())
        issue = next(
            issue for issue in quick_check.get_json()["result"]["issues"]
            if issue["category"] == "Terminology"
        )
        self.assertEqual(issue["location"], {"start": 2, "end": 7})

        audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Whitespace preservation",
            "text": source_text,
        })
        self.assertEqual(audit.status_code, 200, audit.get_json())
        content_id = audit.get_json()["content_id"]
        details = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(details["content"]["original_text"], source_text)
        self.assertEqual(details["versions"][0]["text"], source_text)

        draft = self.client.post("/api/content", json={
            "brand_id": brand_id,
            "title": "Whitespace draft",
            "text": source_text,
        })
        self.assertEqual(draft.status_code, 200, draft.get_json())
        draft_details = self.client.get(
            f"/api/content/{draft.get_json()['content_id']}"
        ).get_json()
        self.assertEqual(draft_details["content"]["original_text"], source_text)
        self.assertEqual(draft_details["versions"][0]["text"], source_text)

    def test_dashboard_uses_only_each_contents_latest_analysis(self):
        brand_id = self.create_brand()
        term_response = self.client.post(
            f"/api/brands/{brand_id}/terminology",
            json={"term": "cheap", "replacement_text": "affordable"},
        )
        self.assertEqual(term_response.status_code, 200, term_response.get_json())

        first_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Corrected later",
            "text": "cheap",
        }).get_json()
        updated_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "content_id": first_audit["content_id"],
            "title": "Corrected later",
            "text": "affordable",
        })
        self.assertEqual(updated_audit.status_code, 200, updated_audit.get_json())

        stats = self.client.get(
            f"/api/history/dashboard-stats?brand_id={brand_id}"
        ).get_json()
        self.assertEqual(stats["violation_categories"]["Terminology"], 0)
        self.assertEqual(stats["top_prohibited_terms"], [])

        second_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Current violation",
            "text": "cheap",
        })
        self.assertEqual(second_audit.status_code, 200, second_audit.get_json())
        stats = self.client.get(
            f"/api/history/dashboard-stats?brand_id={brand_id}"
        ).get_json()
        self.assertEqual(stats["violation_categories"]["Terminology"], 1)
        self.assertEqual(stats["top_prohibited_terms"], [{"term": "cheap", "count": 1}])

    def test_review_is_bound_to_the_analyzed_version_and_records_its_version(self):
        brand_id = self.create_brand()
        first_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Versioned review",
            "text": "First draft.",
        }).get_json()
        content_id = first_audit["content_id"]

        first_review = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "content_version": first_audit["version"],
            "reviewer_name": "Reviewer",
            "decision": "approved",
        })
        self.assertEqual(first_review.status_code, 200, first_review.get_json())
        detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(detail["reviews"][0]["content_version"], 1)

        second_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "content_id": content_id,
            "title": "Versioned review",
            "text": "Updated draft.",
        }).get_json()
        stale_review = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "content_version": first_audit["version"],
            "reviewer_name": "Reviewer",
            "decision": "approved",
        })
        self.assertEqual(stale_review.status_code, 409, stale_review.get_json())
        self.assertEqual(
            self.client.get(f"/api/content/{content_id}").get_json()["reviews"],
            detail["reviews"],
        )

        legacy_review = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "reviewer_name": "Legacy Client",
            "decision": "changes_requested",
        })
        self.assertEqual(legacy_review.status_code, 200, legacy_review.get_json())
        latest_detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(latest_detail["reviews"][0]["content_version"], second_audit["version"])

    def test_invalid_recent_review_limits_and_missing_guidelines_are_reported(self):
        for invalid_limit in ("abc", "0", "101", "-1"):
            with self.subTest(limit=invalid_limit):
                response = self.client.get(f"/api/reviews/recent?limit={invalid_limit}")
                self.assertEqual(response.status_code, 400, response.get_json())

        valid_limit = self.client.get("/api/reviews/recent?limit=1")
        self.assertEqual(valid_limit.status_code, 200, valid_limit.get_json())

        missing_guidelines = (
            "/api/brands/facts/999",
            "/api/brands/terminology/999",
            "/api/brands/claims/999",
        )
        for path in missing_guidelines:
            with self.subTest(path=path):
                response = self.client.delete(path)
                self.assertEqual(response.status_code, 404, response.get_json())
                self.assertFalse(response.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
