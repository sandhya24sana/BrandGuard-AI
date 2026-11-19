import json
import logging
from flask import Blueprint, jsonify
from api_utils import is_positive_int, json_object_payload, validate_string_fields
from database import db_session, dict_from_row
from services.correction_engine import apply_corrections as apply_correction_plan
from services.analyzer import GovernanceAnalyzer

logger = logging.getLogger(__name__)

analysis_bp = Blueprint("analysis", __name__, url_prefix="/api/analysis")
analyzer = GovernanceAnalyzer()

@analysis_bp.route("/system-status", methods=["GET"])
def system_status():
    try:
        ai_configured = analyzer.ai_service.is_configured()
        provider = analyzer.ai_service.get_provider_name()
        return jsonify({
            "success": True,
            "ai_configured": ai_configured,
            "active_engine": provider,
            "mode": "Gemini AI configured; rule-based fallback available" if ai_configured else "Rule-Based Fallback",
            "offline_ready": True
        })
    except Exception as e:
        logger.exception("Error in system-status: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500

@analysis_bp.route("/quick-check", methods=["POST"])
def quick_check():
    data = json_object_payload()
    validate_string_fields(data, ("text", "content_type", "channel"))
    brand_id = data.get("brand_id")
    text = data.get("text", "")
    if not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "A valid brand_id is required"}), 400
    if not isinstance(text, str):
        return jsonify({"success": False, "error": "Content text must be a string"}), 400
    content_type = data.get("content_type") or "Ad Copy"
    channel = data.get("channel") or "Digital Ads"

    if not text.strip():
        return jsonify({"success": False, "error": "Content text is required for quick check"}), 400

    try:
        result = analyzer.analyze_content(brand_id, text, content_type, channel)
        return jsonify({"success": True, "result": result})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.exception("Error in quick_check: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500

@analysis_bp.route("/audit", methods=["POST"])
@analysis_bp.route("/run", methods=["POST"])
def audit_content():
    """
    Unified, atomic content audit endpoint.
    Accepts text, brand_id, title, content_type, channel, and optional content_id.
    Creates or updates content item, runs analysis, records version, and returns result.
    """
    data = json_object_payload()
    validate_string_fields(data, ("title", "content_type", "channel", "text"))
    content_id = data.get("content_id")
    brand_id = data.get("brand_id")
    title = data.get("title", "")
    content_type = data.get("content_type") or "Ad Copy"
    channel = data.get("channel") or "Digital Ads"
    text = data.get("text", "")

    if not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "A valid brand_id is required"}), 400
    if not isinstance(text, str):
        return jsonify({"success": False, "error": "Content text must be a string"}), 400
    if content_id is not None and not is_positive_int(content_id):
        return jsonify({"success": False, "error": "A valid content_id is required"}), 400
    title = title.strip() if isinstance(title, str) else ""
    if not text.strip():
        return jsonify({"success": False, "error": "Content text is required for analysis."}), 400

    try:
        existing_content = None
        if content_id:
            with db_session() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT brand_id, current_version FROM content_items WHERE id = ?",
                    (content_id,)
                )
                existing_content = cursor.fetchone()
            if not existing_content:
                return jsonify({"success": False, "error": "Content item not found"}), 404
            if existing_content["brand_id"] != brand_id:
                return jsonify({"success": False, "error": "Content does not belong to the selected brand"}), 400

        # Run analysis (AI or Rule-based fallback)
        result = analyzer.analyze_content(brand_id, text, content_type, channel)

        with db_session() as conn:
            cursor = conn.cursor()

            if content_id:
                version_num = existing_content["current_version"] + 1
                cursor.execute("""
                    UPDATE content_items
                    SET original_text = ?,
                        risk_score = ?,
                        risk_level = ?,
                        corrected_text = ?,
                        content_type = ?,
                        channel = ?,
                        current_version = ?,
                        status = 'pending_review',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND current_version = ?
                """, (
                    text, result["score"], result["risk_level"], result["corrected_text"],
                    content_type, channel, version_num, content_id,
                    existing_content["current_version"],
                ))
                if cursor.rowcount != 1:
                    return jsonify({
                        "success": False,
                        "error": "Content changed while the audit was running. Run the audit again.",
                    }), 409
                target_id = content_id
            else:
                cursor.execute("""
                    INSERT INTO content_items (brand_id, title, content_type, channel, original_text, corrected_text, current_version, status, risk_score, risk_level)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 'pending_review', ?, ?)
                """, (
                    brand_id, title or "Untitled Content", content_type, channel,
                    text, result["corrected_text"], result["score"], result["risk_level"]
                ))
                target_id = cursor.lastrowid
                version_num = 1

            cursor.execute("""
                INSERT INTO content_versions (content_id, version_number, text, corrected_text, risk_score, risk_level)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                target_id, version_num, text, result["corrected_text"],
                result["score"], result["risk_level"]
            ))

            # Save analysis results
            cursor.execute("""
                INSERT INTO analysis_results 
                (content_id, version_number, score, risk_level, sub_scores, engine_used, issues, summary, corrected_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                target_id,
                version_num,
                result["score"],
                result["risk_level"],
                json.dumps(result["sub_scores"]),
                result["engine_used"],
                json.dumps(result["issues"]),
                result["summary"],
                result["corrected_text"]
            ))
            analysis_id = cursor.lastrowid

        result["analysis_id"] = analysis_id
        return jsonify({
            "success": True,
            "content_id": target_id,
            "version": version_num,
            "status": "pending_review",
            "result": result
        })
    except Exception as e:
        logger.exception("Error in audit_content: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500

@analysis_bp.route("/run/<content_id>", methods=["POST"])
def run_analysis_by_id(content_id):
    """Fallback route for existing content item by ID."""
    try:
        cid = int(content_id)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": f"Invalid content ID: '{content_id}'. Must be an integer."}), 400

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM content_items WHERE id = ?", (cid,))
            content_row = cursor.fetchone()
            if not content_row:
                return jsonify({"success": False, "error": f"Content item with ID {cid} not found"}), 404
            content = dict_from_row(content_row)

        result = analyzer.analyze_content(
            content["brand_id"], 
            content["original_text"], 
            content["content_type"], 
            content["channel"]
        )

        version_num = content["current_version"] + 1
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE content_items
                SET current_version = ?,
                    status = 'pending_review',
                    risk_score = ?,
                    risk_level = ?,
                    corrected_text = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND current_version = ?
            """, (
                version_num,
                result["score"],
                result["risk_level"],
                result["corrected_text"],
                cid,
                content["current_version"],
            ))
            if cursor.rowcount != 1:
                return jsonify({
                    "success": False,
                    "error": "Content changed while the analysis was running. Run the audit again.",
                }), 409

            cursor.execute("""
                INSERT INTO content_versions (content_id, version_number, text, corrected_text, risk_score, risk_level)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                cid, version_num, content["original_text"], result["corrected_text"],
                result["score"], result["risk_level"]
            ))

            cursor.execute("""
                INSERT INTO analysis_results 
                (content_id, version_number, score, risk_level, sub_scores, engine_used, issues, summary, corrected_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cid,
                version_num,
                result["score"],
                result["risk_level"],
                json.dumps(result["sub_scores"]),
                result["engine_used"],
                json.dumps(result["issues"]),
                result["summary"],
                result["corrected_text"]
            ))
            analysis_id = cursor.lastrowid

        result["analysis_id"] = analysis_id
        return jsonify({
            "success": True, "content_id": cid, "version": version_num,
            "status": "pending_review", "result": result
        })
    except Exception as e:
        logger.exception("Error in run_analysis_by_id: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500

@analysis_bp.route("/apply-corrections/<int:content_id>", methods=["POST"])
def apply_corrections(content_id):
    data = json_object_payload()
    apply_all = data.get("apply_all", False)
    issue_ids = data.get("issue_ids")
    legacy_text = data.get("corrected_text")
    if not isinstance(apply_all, bool):
        return jsonify({"success": False, "error": "apply_all must be a boolean"}), 400
    if issue_ids is not None and (
        not isinstance(issue_ids, list)
        or not issue_ids
        or not all(isinstance(issue_id, str) and issue_id for issue_id in issue_ids)
    ):
        return jsonify({"success": False, "error": "issue_ids must be a non-empty list of issue IDs"}), 400
    if apply_all and issue_ids is not None:
        return jsonify({"success": False, "error": "Choose either apply_all or issue_ids"}), 400
    if legacy_text is not None and (apply_all or issue_ids is not None):
        return jsonify({"success": False, "error": "Choose one correction request format"}), 400
    if issue_ids is None and not apply_all and legacy_text is None:
        return jsonify({"success": False, "error": "A correction selection is required"}), 400
    if legacy_text is not None and (
        not isinstance(legacy_text, str)
        or not legacy_text
        or len(legacy_text) > 100_000
    ):
        return jsonify({"success": False, "error": "corrected_text must contain 1 to 100000 characters"}), 400

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM content_items WHERE id = ?", (content_id,))
            content_row = cursor.fetchone()
            if not content_row:
                return jsonify({"success": False, "error": f"Content item {content_id} not found"}), 404
            content = dict_from_row(content_row)

        source_text = content["original_text"]
        source_analysis = analyzer.analyze_content(
            content["brand_id"],
            source_text,
            content["content_type"],
            content["channel"],
        )
        issues = source_analysis.get("issues", [])
        applied_issue_ids = []
        correction_plan = None

        if apply_all or issue_ids is not None:
            requested_ids = None if apply_all else list(dict.fromkeys(issue_ids))
            if requested_ids is not None:
                available_ids = {issue.get("id") for issue in issues}
                missing_ids = sorted(set(requested_ids) - available_ids)
                if missing_ids:
                    return jsonify({
                        "success": False,
                        "error": "The selected issue is no longer present. Run the audit again.",
                        "stale_issue_ids": missing_ids,
                    }), 409
            correction_plan = apply_correction_plan(source_text, issues, requested_ids)
            new_text = correction_plan["text"]
            applied_issue_ids = correction_plan["applied_issue_ids"]
            if requested_ids is not None and set(applied_issue_ids) != set(requested_ids):
                return jsonify({
                    "success": False,
                    "error": "The selected issue has no safe, current replacement and requires manual review.",
                    "manual_review": correction_plan["manual_review"],
                }), 409
            if not applied_issue_ids:
                return jsonify({
                    "success": False,
                    "error": "There are no safe corrections to apply.",
                    "manual_review": correction_plan["manual_review"],
                }), 409
        else:
            # Keep the pre-existing API payload available for older clients and explicit edits.
            new_text = legacy_text
            if new_text == source_text:
                return jsonify({"success": False, "error": "The submitted text has no changes."}), 409

        new_version = content["current_version"] + 1

        # Run fresh analysis on new text
        result = analyzer.analyze_content(
            content["brand_id"], 
            new_text, 
            content["content_type"], 
            content["channel"]
        )
        unresolved_applied_issue_ids = []
        if correction_plan:
            previous_issues = {
                issue.get("id"): issue for issue in issues
                if issue.get("id") in applied_issue_ids
            }
            for replacement in correction_plan["replacement_locations"]:
                for issue_id in replacement["issue_ids"]:
                    previous_issue = previous_issues.get(issue_id, {})
                    still_present = any(
                        issue.get("category") == previous_issue.get("category")
                        and issue.get("rule") == previous_issue.get("rule")
                        and (issue.get("location") or {}).get("start") == replacement["start"]
                        and (issue.get("location") or {}).get("end") == replacement["end"]
                        for issue in result.get("issues", [])
                    )
                    if still_present:
                        unresolved_applied_issue_ids.append(issue_id)

        correction_review = {
            "manual_review_required": True,
            "grammar_validated": False,
            "remaining_issue_count": len(result.get("issues", [])),
            "unresolved_applied_issue_ids": unresolved_applied_issue_ids,
        }

        with db_session() as conn:
            cursor = conn.cursor()
            # Update content item
            cursor.execute("""
                UPDATE content_items
                SET original_text = ?,
                    corrected_text = ?,
                    current_version = ?,
                    risk_score = ?,
                    risk_level = ?,
                    status = 'pending_review',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND current_version = ?
            """, (
                new_text,
                result["corrected_text"],
                new_version,
                result["score"],
                result["risk_level"],
                content_id,
                content["current_version"],
            ))
            if cursor.rowcount != 1:
                return jsonify({
                    "success": False,
                    "error": "Content changed while the correction was being prepared. Run the audit again.",
                }), 409

            # Record new version
            cursor.execute("""
                INSERT INTO content_versions (content_id, version_number, text, corrected_text, risk_score, risk_level)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (content_id, new_version, new_text, result["corrected_text"], result["score"], result["risk_level"]))

            # Record analysis result
            cursor.execute("""
                INSERT INTO analysis_results 
                (content_id, version_number, score, risk_level, sub_scores, engine_used, issues, summary, corrected_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                content_id,
                new_version,
                result["score"],
                result["risk_level"],
                json.dumps(result["sub_scores"]),
                result["engine_used"],
                json.dumps(result["issues"]),
                result["summary"],
                result["corrected_text"]
            ))

        return jsonify({
            "success": True, 
            "new_version": new_version, 
            "applied_text": new_text,
            "applied_issue_ids": applied_issue_ids,
            "correction_review": correction_review,
            "result": result, 
            "message": f"Applied corrections and updated to version {new_version}."
        })
    except Exception as e:
        logger.exception("Error in apply_corrections: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500
