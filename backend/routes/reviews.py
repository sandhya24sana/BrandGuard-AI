import logging
from flask import Blueprint, request, jsonify
from api_utils import is_positive_int, json_object_payload, validate_string_fields
from database import db_session, dict_from_row

logger = logging.getLogger(__name__)

reviews_bp = Blueprint("reviews", __name__, url_prefix="/api/reviews")

@reviews_bp.route("", methods=["POST"])
def submit_review():
    data = json_object_payload()
    validate_string_fields(data, ("reviewer_name", "reviewer_role", "decision", "comments"))
    content_id = data.get("content_id")
    reviewer_name = data.get("reviewer_name", "")
    reviewer_role = data.get("reviewer_role", "")
    decision = data.get("decision", "")
    comments = data.get("comments", "")
    content_version = data.get("content_version")
    if not is_positive_int(content_id):
        return jsonify({"success": False, "error": "A valid content_id is required"}), 400
    if content_version is not None and not is_positive_int(content_version):
        return jsonify({"success": False, "error": "content_version must be a positive integer"}), 400
    if not isinstance(reviewer_name, str) or not reviewer_name.strip():
        return jsonify({"success": False, "error": "Reviewer name is required"}), 400
    if not isinstance(reviewer_role, str) or not isinstance(comments, str) or not isinstance(decision, str):
        return jsonify({"success": False, "error": "Review fields must be strings"}), 400
    reviewer_name = reviewer_name.strip()
    reviewer_role = reviewer_role.strip()
    decision = decision.lower().strip()
    comments = comments.strip()

    valid_decisions = {"approved", "rejected", "changes_requested"}
    if decision not in valid_decisions:
        return jsonify({"success": False, "error": f"Invalid decision. Must be one of: {', '.join(valid_decisions)}"}), 400

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT c.current_version, EXISTS(
                    SELECT 1 FROM analysis_results a
                    WHERE a.content_id = c.id
                      AND a.version_number = c.current_version
                ) AS has_current_analysis
                FROM content_items c WHERE c.id = ?
            """, (content_id,))
            content = cursor.fetchone()
            if not content:
                return jsonify({"success": False, "error": f"Content item {content_id} not found"}), 404
            if not content["has_current_analysis"]:
                return jsonify({
                    "success": False,
                    "error": "Analyze the current content version before recording a human review decision"
                }), 409
            current_version = content["current_version"]
            if content_version is not None and content_version != current_version:
                return jsonify({
                    "success": False,
                    "error": "Content changed after this review was opened. Review the latest version.",
                    "current_version": current_version,
                }), 409

            # Update content status
            cursor.execute("""
                UPDATE content_items
                SET status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND current_version = ?
            """, (decision, content_id, current_version))
            if cursor.rowcount != 1:
                return jsonify({
                    "success": False,
                    "error": "Content changed while the review was being recorded. Review the latest version.",
                }), 409

            # Insert review audit log
            cursor.execute("""
                INSERT INTO reviews (
                    content_id, content_version, reviewer_name, reviewer_role, decision, comments
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                content_id, current_version, reviewer_name, reviewer_role, decision, comments
            ))
            review_id = cursor.lastrowid

        return jsonify({
            "success": True,
            "review_id": review_id,
            "new_status": decision,
            "message": f"Content successfully marked as '{decision.replace('_', ' ').title()}' by {reviewer_name}."
        })
    except Exception as e:
        logger.exception("Error in submit_review: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500

@reviews_bp.route("/content/<int:content_id>", methods=["GET"])
def get_content_reviews(content_id):
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM reviews 
                WHERE content_id = ? 
                ORDER BY created_at DESC
            """, (content_id,))
            reviews = [dict_from_row(r) for r in cursor.fetchall()]
        return jsonify({"success": True, "reviews": reviews})
    except Exception as e:
        logger.exception("Error in get_content_reviews: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500

@reviews_bp.route("/recent", methods=["GET"])
def get_recent_reviews():
    raw_limit = request.args.get("limit", "20")
    try:
        limit = int(raw_limit)
    except ValueError:
        return jsonify({"success": False, "error": "limit must be an integer from 1 to 100"}), 400
    if not 1 <= limit <= 100:
        return jsonify({"success": False, "error": "limit must be an integer from 1 to 100"}), 400
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.*, c.title as content_title, c.content_type, b.name as brand_name
                FROM reviews r
                JOIN content_items c ON r.content_id = c.id
                JOIN brands b ON c.brand_id = b.id
                ORDER BY r.created_at DESC
                LIMIT ?
            """, (limit,))
            reviews = [dict_from_row(r) for r in cursor.fetchall()]
        return jsonify({"success": True, "reviews": reviews})
    except Exception as e:
        logger.exception("Error in get_recent_reviews: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500
