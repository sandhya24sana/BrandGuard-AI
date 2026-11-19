import logging
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from database import db_session, dict_from_row
from api_utils import is_positive_int, json_object_payload, validate_string_fields

logger = logging.getLogger(__name__)

content_bp = Blueprint("content", __name__, url_prefix="/api/content")

ALLOWED_EXTENSIONS = {"txt", "md", "markdown", "docx", "pdf", "json", "csv"}

def allowed_file(filename):
    return (
        isinstance(filename, str)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )

def extract_text_from_file(file_storage, filename):
    ext = filename.rsplit(".", 1)[1].lower()
    
    if ext in ("txt", "md", "markdown", "json", "csv"):
        try:
            return file_storage.read().decode("utf-8")
        except UnicodeDecodeError:
            file_storage.seek(0)
            return file_storage.read().decode("latin-1")

    elif ext == "pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_storage)
            text_parts = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return "\n\n".join(text_parts)
        except Exception as e:
            raise ValueError(f"Could not read PDF file: {str(e)}")

    elif ext == "docx":
        try:
            import docx
            doc = docx.Document(file_storage)
            return "\n\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        except Exception as e:
            raise ValueError(f"Could not read DOCX file: {str(e)}")

    raise ValueError(f"Unsupported file format: {ext}")

@content_bp.route("/upload", methods=["POST"])
def upload_document():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"success": False, "error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"success": False, "error": f"File type not supported. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    filename = secure_filename(file.filename)
    try:
        extracted_text = extract_text_from_file(file, filename)
        return jsonify({
            "success": True,
            "filename": filename,
            "text": extracted_text,
            "character_count": len(extracted_text),
            "word_count": len(extracted_text.split())
        })
    except Exception as e:
        logger.exception("Upload error: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 400

@content_bp.route("", methods=["GET"])
def list_content():
    brand_id = request.args.get("brand_id")
    status = request.args.get("status")
    channel = request.args.get("channel")

    query = """
        SELECT c.*, b.name as brand_name 
        FROM content_items c
        JOIN brands b ON c.brand_id = b.id
        WHERE 1=1
    """
    params = []

    if brand_id is not None:
        try:
            brand_id = int(brand_id)
        except ValueError:
            return jsonify({"success": False, "error": "brand_id must be an integer"}), 400
        if brand_id < 1:
            return jsonify({"success": False, "error": "brand_id must be positive"}), 400
        query += " AND c.brand_id = ?"
        params.append(brand_id)
    if status:
        query += " AND c.status = ?"
        params.append(status)
    if channel:
        query += " AND c.channel = ?"
        params.append(channel)

    query += " ORDER BY c.updated_at DESC"

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            items = [dict_from_row(r) for r in cursor.fetchall()]
        return jsonify({"success": True, "items": items})
    except Exception as e:
        logger.exception("List content error: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500

@content_bp.route("/<int:content_id>", methods=["GET"])
def get_content_detail(content_id):
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT c.*, b.name as brand_name, b.primary_tone, b.tagline
                FROM content_items c
                JOIN brands b ON c.brand_id = b.id
                WHERE c.id = ?
            """, (content_id,))
            item = cursor.fetchone()
            if not item:
                return jsonify({"success": False, "error": "Content item not found"}), 404

            item_dict = dict_from_row(item)

            # Fetch latest analysis
            cursor.execute("""
                SELECT * FROM analysis_results 
                WHERE content_id = ? 
                ORDER BY id DESC LIMIT 1
            """, (content_id,))
            analysis_row = cursor.fetchone()
            analysis = dict_from_row(analysis_row) if analysis_row else None

            if analysis:
                import json
                for k in ["sub_scores", "issues"]:
                    if analysis.get(k) and isinstance(analysis[k], str):
                        try:
                            analysis[k] = json.loads(analysis[k])
                        except:
                            pass

            # Fetch version history
            cursor.execute("""
                SELECT * FROM content_versions 
                WHERE content_id = ? 
                ORDER BY version_number DESC
            """, (content_id,))
            versions = [dict_from_row(r) for r in cursor.fetchall()]

            # Fetch review history
            cursor.execute("""
                SELECT * FROM reviews 
                WHERE content_id = ? 
                ORDER BY id DESC
            """, (content_id,))
            reviews = [dict_from_row(r) for r in cursor.fetchall()]

        return jsonify({
            "success": True,
            "content": item_dict,
            "latest_analysis": analysis,
            "versions": versions,
            "reviews": reviews
        })
    except Exception as e:
        logger.exception("Get content detail error: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500

@content_bp.route("", methods=["POST"])
def create_content():
    data = json_object_payload()
    validate_string_fields(data, ("title", "content_type", "channel", "text"))
    brand_id = data.get("brand_id")
    title = data.get("title", "")
    text = data.get("text", "")

    if not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "A valid brand_id is required"}), 400
    if not isinstance(text, str):
        return jsonify({"success": False, "error": "Content text must be a string"}), 400
    title = title.strip() if isinstance(title, str) else ""
    content_type = data.get("content_type") or "Ad Copy"
    channel = data.get("channel") or "Digital Ads"
    if not text.strip():
        return jsonify({"success": False, "error": "Content text is required"}), 400

    try:
        with db_session() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO content_items (brand_id, title, content_type, channel, original_text, current_version, status)
                VALUES (?, ?, ?, ?, ?, 1, 'draft')
            """, (brand_id, title or "Untitled Draft", content_type, channel, text))
            content_id = cursor.lastrowid

            # Create version 1 record
            cursor.execute("""
                INSERT INTO content_versions (content_id, version_number, text)
                VALUES (?, 1, ?)
            """, (content_id, text))

        return jsonify({
            "success": True, 
            "content_id": content_id,
            "message": "Content created successfully"
        })
    except Exception as e:
        logger.exception("Create content error: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500
