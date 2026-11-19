import json
import logging
from typing import Dict, Any
from database import db_session, dict_from_row
from services.rules_engine import RuleBasedAnalyzer
from services.ai_service import AIService

logger = logging.getLogger(__name__)

class GovernanceAnalyzer:
    """
    Main BrandGuard Governance Engine.
    Coordinates database retrieval of active brand guidelines,
    invokes AI or Rule-Based engine, and merges results.
    """

    def __init__(self):
        self.ai_service = AIService()

    def analyze_content(self, brand_id: int, text: str, content_type: str = "Ad Copy", 
                        channel: str = "Digital Ads") -> Dict[str, Any]:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM brands WHERE id = ?", (brand_id,))
            brand_row = cursor.fetchone()
            if not brand_row:
                raise ValueError(f"Brand ID {brand_id} not found.")
            brand = dict_from_row(brand_row)

            cursor.execute("SELECT * FROM brand_facts WHERE brand_id = ? AND active = 1", (brand_id,))
            facts = [dict_from_row(row) for row in cursor.fetchall()]
            cursor.execute("SELECT * FROM brand_terminology WHERE brand_id = ? AND active = 1", (brand_id,))
            terminology = [dict_from_row(row) for row in cursor.fetchall()]
            cursor.execute("SELECT * FROM brand_visual_rules WHERE brand_id = ?", (brand_id,))
            visual_row = cursor.fetchone()
            visual_rules = dict_from_row(visual_row) if visual_row else {}
            for key in ("primary_colors", "secondary_colors", "forbidden_colors"):
                if isinstance(visual_rules.get(key), str):
                    try:
                        visual_rules[key] = json.loads(visual_rules[key])
                    except json.JSONDecodeError:
                        logger.error("Stored visual guideline %s for brand %s is invalid JSON", key, brand_id)
                        visual_rules[key] = []
            cursor.execute("SELECT * FROM brand_claim_rules WHERE brand_id = ? AND active = 1", (brand_id,))
            claim_rules = [dict_from_row(row) for row in cursor.fetchall()]

        # Always run the deterministic rule engine as the foundational ground truth
        rule_engine = RuleBasedAnalyzer(brand, facts, terminology, visual_rules, claim_rules)
        rule_result = rule_engine.analyze(text, content_type, channel)

        # Check if AI is configured and attempt AI analysis
        ai_result = None
        if self.ai_service.is_configured():
            ai_result = self.ai_service.analyze_with_ai(
                text, brand, facts, terminology, claim_rules, visual_rules, content_type, channel
            )

        # If AI succeeded, blend AI with rule-based to ensure no hard rules were bypassed
        if ai_result:
            result = self._merge_results(rule_result, ai_result)
        else:
            result = rule_result

        result["visual_validation"] = {
            "supported": False,
            "message": "Visual guidelines are stored for reference; image and layout validation is not implemented."
        }
        return result

    def _merge_results(self, rule_res: Dict[str, Any], ai_res: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merges AI insights with strict deterministic rule enforcement.
        Ensures 100% detection of banned terms and verified fact discrepancies.
        """
        combined_issues = list(rule_res.get("issues", []))
        existing_matches = {
            (
                iss.get("category"),
                iss.get("matched_text", "").casefold(),
                (iss.get("location") or {}).get("start"),
                (iss.get("location") or {}).get("end"),
            )
            for iss in combined_issues
        }

        # Add AI issues that weren't caught by simple rules
        for ai_issue in ai_res.get("issues", []):
            matched = ai_issue.get("matched_text", "")
            location = ai_issue.get("location") or {}
            identity = (
                ai_issue.get("category"),
                matched.casefold(),
                location.get("start"),
                location.get("end"),
            )
            if matched and identity not in existing_matches:
                ai_issue["source"] = "AI"
                ai_issue["replacement_text"] = None
                ai_issue["auto_fixable"] = False
                ai_issue["manual_review_required"] = True
                combined_issues.append(ai_issue)
                existing_matches.add(identity)

        final_score = min(rule_res["score"], ai_res["score"])
        severities = {issue.get("severity", "").upper() for issue in combined_issues}
        if "CRITICAL" in severities:
            final_score = min(final_score, 49)
        elif "WARNING" in severities:
            final_score = min(final_score, 89)

        if final_score >= 90:
            risk_level = "LOW"
            risk_badge = "Low Detected Risk - Human Review Required"
        elif final_score >= 75:
            risk_level = "MODERATE"
            risk_badge = "Moderate Risk - Revisions Recommended"
        elif final_score >= 50:
            risk_level = "HIGH"
            risk_badge = "High Risk - Substantial Issues Detected"
        else:
            risk_level = "CRITICAL"
            risk_badge = "Critical Risk - Immediate Remediation Required"

        category_scores = {
            "Terminology": "terminology",
            "Fact Accuracy": "facts",
            "Voice & Tone": "tone",
            "Compliance & Claims": "claims",
            "Accessibility & Clarity": "accessibility"
        }
        sub_scores = dict(ai_res.get("sub_scores") or rule_res.get("sub_scores", {}))
        for issue in combined_issues:
            score_name = category_scores.get(issue.get("category"))
            severity = issue.get("severity", "").upper()
            if score_name and severity in ("CRITICAL", "WARNING"):
                ceiling = 49 if severity == "CRITICAL" else 74
                sub_scores[score_name] = min(sub_scores.get(score_name, 100), ceiling)

        if final_score >= 90:
            risk_level, risk_badge = "LOW", "Low Detected Risk - Human Review Required"
        elif final_score >= 75:
            risk_level, risk_badge = "MODERATE", "Moderate Risk - Revisions Recommended"
        elif final_score >= 50:
            risk_level, risk_badge = "HIGH", "High Risk - Substantial Issues Detected"
        else:
            risk_level, risk_badge = "CRITICAL", "Critical Risk - Immediate Remediation Required"

        critical_count = sum(issue.get("severity", "").upper() == "CRITICAL" for issue in combined_issues)
        warning_count = sum(issue.get("severity", "").upper() == "WARNING" for issue in combined_issues)
        info_count = sum(issue.get("severity", "").upper() == "INFO" for issue in combined_issues)
        if critical_count:
            assessment = "Critical policy violations were detected."
        elif warning_count:
            assessment = "Policy warnings were detected."
        elif info_count:
            assessment = "Only informational observations were detected."
        else:
            assessment = "No configured policy violations were detected."

        corrected_text = rule_res.get("corrected_text", "")
        return {
            "score": final_score,
            "risk_level": risk_level,
            "risk_badge": risk_badge,
            "sub_scores": sub_scores,
            "readability": rule_res.get("readability"),
            "issues": combined_issues,
            "summary": (
                f"{assessment} Score: {final_score}/100 ({risk_level} risk); "
                f"{critical_count} critical, {warning_count} warning, and {info_count} informational issue(s). "
                "This automated result is not an approval; human review is required before publication."
            ),
            "corrected_text": corrected_text,
            "engine_used": "AI_GEMINI"
        }
