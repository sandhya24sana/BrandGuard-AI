
import json
import logging
from flask import Blueprint, request, jsonify
from database import db_session, dict_from_row

logger = logging.getLogger(__name__)

history_bp = Blueprint("history", __name__, url_prefix="/api/history")


@history_bp.route("/dashboard-stats", methods=["GET"])
def dashboard_stats():
    brand_id = request.args.get("brand_id")

    if brand_id is not None:
        try:
            brand_id = int(brand_id)
        except ValueError:
            return jsonify({"success": False, "error": "brand_id must be an integer"}), 400

        if brand_id < 1:
            return jsonify({"success": False, "error": "brand_id must be positive"}), 400

    base_filter = " WHERE brand_id = ?" if brand_id is not None else ""
    params = [brand_id] if brand_id is not None else []

    try:
        with db_session() as conn:
            cursor = conn.cursor()

            # Total scanned
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM content_items{base_filter}",
                params
            )
            total_scanned = cursor.fetchone()["total"]

            # Average score
            score_filter = (
                "WHERE brand_id = ? AND risk_score > 0"
                if brand_id is not None
                else "WHERE risk_score > 0"
            )
            cursor.execute(
                f"SELECT AVG(risk_score) AS avg_score FROM content_items {score_filter}",
                params
            )
            avg_row = cursor.fetchone()
            avg_score = round(avg_row["avg_score"] or 0, 1)

            # Status counts
            cursor.execute(
                f"""
                SELECT status, COUNT(*) AS count
                FROM content_items{base_filter}
                GROUP BY status
                """,
                params
            )
            status_counts = {
                row["status"]: row["count"]
                for row in cursor.fetchall()
            }

            # Recent items
            recent_filter = "WHERE c.brand_id = ?" if brand_id is not None else ""
            cursor.execute(
                f"""
                SELECT c.*, b.name AS brand_name
                FROM content_items c
                JOIN brands b ON c.brand_id = b.id
                {recent_filter}
                ORDER BY c.updated_at DESC
                LIMIT 6
                """,
                params
            )
            recent_items = [
                dict_from_row(row)
                for row in cursor.fetchall()
            ]

            # Latest analysis issues, optionally filtered by brand
            brand_filter = (
                "AND c.brand_id = ?"
                if brand_id is not None
                else ""
            )

            issue_params = (brand_id,) if brand_id is not None else ()

            cursor.execute(
                f"""
                SELECT ar.issues
                FROM analysis_results ar
                JOIN content_items c ON c.id = ar.content_id
                WHERE 1=1
                  {brand_filter}
                  AND ar.id = (
                      SELECT MAX(latest.id)
                      FROM analysis_results latest
                      WHERE latest.content_id = ar.content_id
                  )
                ORDER BY ar.id DESC
                LIMIT 50
                """,
                issue_params
            )
            rows = cursor.fetchall()

        violation_categories = {
            "Terminology": 0,
            "Fact Accuracy": 0,
            "Voice & Tone": 0,
            "Compliance & Claims": 0,
            "Accessibility & Clarity": 0
        }

        top_prohibited_terms = {}

        for row in rows:
            try:
                issues = (
                    json.loads(row["issues"])
                    if isinstance(row["issues"], str)
                    else row["issues"]
                )

                if not isinstance(issues, list):
                    continue

                for issue in issues:
                    if not isinstance(issue, dict):
                        continue

                    category = issue.get("category", "Terminology")

                    violation_categories[category] = (
                        violation_categories.get(category, 0) + 1
                    )

                    if category == "Terminology":
                        term = issue.get("matched_text", "").lower()
                        if term:
                            top_prohibited_terms[term] = (
                                top_prohibited_terms.get(term, 0) + 1
                            )

            except (TypeError, ValueError, AttributeError) as error:
                logger.warning(
                    "Skipping malformed stored analysis issues: %s",
                    error
                )

        sorted_terms = sorted(
            top_prohibited_terms.items(),
            key=lambda item: item[1],
            reverse=True
        )[:5]

        return jsonify({
            "success": True,
            "total_scanned": total_scanned,
            "average_score": avg_score,
            "status_breakdown": status_counts,
            "violation_categories": violation_categories,
            "top_prohibited_terms": [
                {"term": term, "count": count}
                for term, count in sorted_terms
            ],
            "recent_items": recent_items
        })

    except Exception as e:
        logger.exception("Error in dashboard_stats: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


@history_bp.route("/export/<int:content_id>", methods=["GET"])
def export_audit_report(content_id):
    try:
        with db_session() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT c.*, b.name AS brand_name, b.industry, b.primary_tone
                FROM content_items c
                JOIN brands b ON c.brand_id = b.id
                WHERE c.id = ?
                """,
                (content_id,)
            )

            item = cursor.fetchone()

            if not item:
                return jsonify({
                    "success": False,
                    "error": f"Item {content_id} not found"
                }), 404

            item_dict = dict_from_row(item)

            # Latest analysis
            cursor.execute(
                """
                SELECT *
                FROM analysis_results
                WHERE content_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (content_id,)
            )

            analysis_row = cursor.fetchone()
            analysis = (
                dict_from_row(analysis_row)
                if analysis_row
                else {}
            )

            if analysis:
                for key in ("sub_scores", "issues"):
                    value = analysis.get(key)

                    if value and isinstance(value, str):
                        try:
                            analysis[key] = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(
                                "Could not decode analysis field: %s",
                                key
                            )

            # Review history
            cursor.execute(
                """
                SELECT *
                FROM reviews
                WHERE content_id = ?
                ORDER BY id DESC
                """,
                (content_id,)
            )
            reviews = [
                dict_from_row(row)
                for row in cursor.fetchall()
            ]

            # Version history
            cursor.execute(
                """
                SELECT *
                FROM content_versions
                WHERE content_id = ?
                ORDER BY version_number ASC
                """,
                (content_id,)
            )
            versions = [
                dict_from_row(row)
                for row in cursor.fetchall()
            ]

        report = {
            "report_title": (
                f"BrandGuard AI Compliance Audit - {item_dict['title']}"
            ),
            "generated_at": item_dict.get("updated_at"),

            "brand": {
                "name": item_dict["brand_name"],
                "industry": item_dict["industry"],
                "primary_tone": item_dict["primary_tone"]
            },

            "content_metadata": {
                "title": item_dict["title"],
                "content_type": item_dict["content_type"],
                "channel": item_dict["channel"],
                "status": item_dict["status"],
                "version": item_dict["current_version"]
            },

            "compliance_summary": {
                "overall_score": item_dict["risk_score"],
                "risk_level": item_dict["risk_level"],
                "sub_scores": analysis.get("sub_scores", {}),
                "summary": analysis.get("summary", ""),
                "engine_used": analysis.get(
                    "engine_used",
                    "RULE_BASED_FALLBACK"
                )
            },

            "issues_detected": analysis.get("issues", []),
            "original_text": item_dict["original_text"],
            "corrected_text": item_dict["corrected_text"],
            "review_audit_log": reviews,
            "version_history": versions
        }

        return jsonify({
            "success": True,
            "report": report
        })

    except Exception as e:
        logger.exception("Error in export_audit_report: %s", str(e))
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
