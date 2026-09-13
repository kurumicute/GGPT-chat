"""圖片與一般附件上傳 API。"""

import os
import uuid

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from config import (
    ALLOWED_FILE_EXTENSIONS,
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_SPECIAL_FILENAMES,
    IMAGE_MAX_DIMENSION,
    IMAGE_WEBP_QUALITY,
)
from database import require_login

Image = None
ImageOps = None
try:
    from PIL import Image, ImageOps
except ImportError:
    pass


bp = Blueprint("uploads", __name__)

def _normalized_filename(filename):
    raw = os.path.basename(str(filename or "")).strip()
    safe = secure_filename(raw) or "file"
    return raw, safe


def allowed_image_file(filename):
    raw, safe = _normalized_filename(filename)
    ext = safe.rsplit(".", 1)[1].lower() if "." in safe else ""
    return ext in ALLOWED_IMAGE_EXTENSIONS


def allowed_attachment_file(filename):
    raw, safe = _normalized_filename(filename)
    lower_name = raw.lower()
    safe_lower = safe.lower()

    # 支援無副檔名的特殊開發檔案。
    if lower_name in ALLOWED_SPECIAL_FILENAMES or safe_lower in ALLOWED_SPECIAL_FILENAMES:
        return True

    ext = safe.rsplit(".", 1)[1].lower() if "." in safe else ""
    return bool(ext) and ext in ALLOWED_FILE_EXTENSIONS


def is_uploaded_image(filename, mime_type=""):
    # 圖片判斷以允許的副檔名為主，避免只因瀏覽器 MIME 宣告為 image/* 就放行其他格式。
    return allowed_image_file(filename)


def build_upload_name(filename):
    safe = secure_filename(filename) or "file"
    return f"{uuid.uuid4().hex}_{safe}"


def store_upload(file, safe_name, kind):
    """儲存上傳內容；一般靜態圖片會縮圖並轉為 WebP。"""
    extension = safe_name.rsplit(".", 1)[1].lower() if "." in safe_name else ""

    if kind == "image" and Image is not None and extension in {
        "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"
    }:
        try:
            file.stream.seek(0)
            with Image.open(file.stream) as source:
                # 動圖保持原格式，避免只保留第一幀。
                if getattr(source, "is_animated", False):
                    raise ValueError("animated image")

                image = ImageOps.exif_transpose(source)
                image.thumbnail(
                    (IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION),
                    Image.Resampling.LANCZOS
                )
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")

                optimized_name = f"{uuid.uuid4().hex}.webp"
                optimized_path = os.path.join(current_app.config["UPLOAD_FOLDER"], optimized_name)
                image.save(
                    optimized_path,
                    "WEBP",
                    quality=IMAGE_WEBP_QUALITY,
                    method=6
                )
                return optimized_name, optimized_path, "image/webp"
        except Exception:
            # Pillow 未支援的格式或壓縮失敗時保留原檔，不中斷使用者上傳。
            file.stream.seek(0)

    stored_name = build_upload_name(safe_name)
    stored_path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name)
    file.save(stored_path)
    return stored_name, stored_path, file.mimetype or "application/octet-stream"


def read_text_file(path):
    encodings = ["utf-8", "utf-8-sig", "big5", "cp950", "latin-1"]
    for encoding in encodings:
        try:
            with open(path, "r", encoding=encoding) as f:
                text = f.read()
            return text[:200000]
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


@bp.route("/upload", methods=["POST"])
def upload():
    """統一的圖片 / 一般檔案上傳 API。支援一次上傳多個檔案。"""
    if require_login() is None:
        return jsonify({"success": False, "error": "請先登入！"}), 401

    incoming = request.files.getlist("files") or request.files.getlist("file")
    if not incoming:
        return jsonify({"success": False, "error": "沒有檔案"}), 400

    uploaded = []

    for file in incoming:
        if not file or not file.filename:
            continue

        original_name, safe_name = _normalized_filename(file.filename)
        mime_type = file.mimetype or "application/octet-stream"

        # 圖片與一般附件共用同一個端點，但仍做基本副檔名驗證。
        if is_uploaded_image(original_name, mime_type):
            if not allowed_image_file(original_name) and not mime_type.startswith("image/"):
                return jsonify({
                    "success": False,
                    "error": f"不支援的圖片格式：{original_name}"
                }), 400
            kind = "image"
        else:
            if not allowed_attachment_file(original_name):
                return jsonify({
                    "success": False,
                    "error": f"不支援的檔案類型：{original_name}"
                }), 400
            kind = "file"

        unique_filename, path, stored_mime_type = store_upload(file, safe_name, kind)

        size = os.path.getsize(path)
        content = None

        # 圖片不讀成文字；其餘檔案若看起來像文字/程式檔則讀取前 200,000 字元。
        if kind == "file":
            ext = safe_name.rsplit(".", 1)[1].lower() if "." in safe_name else ""
            if ext in ALLOWED_FILE_EXTENSIONS or safe_name.lower() in ALLOWED_SPECIAL_FILENAMES:
                content = read_text_file(path)

        item = {
            "success": True,
            "kind": kind,
            "name": original_name[:255],
            "safe_name": safe_name[:255],
            "size": size,
            "mime_type": stored_mime_type[:255],
            "content": content,
            "readable": content is not None,
        }

        if kind == "image":
            item["image_url"] = f"/uploads/{unique_filename}"
            item["url"] = item["image_url"]
        else:
            item["file_url"] = f"/uploads/{unique_filename}"
            item["url"] = item["file_url"]

        uploaded.append(item)

    if not uploaded:
        return jsonify({"success": False, "error": "沒有可上傳的檔案。"}), 400

    return jsonify({
        "success": True,
        "files": uploaded,
        # 單一檔案時直接提供向下相容欄位。
        **(uploaded[0] if len(uploaded) == 1 else {})
    })


# 舊版 API 保留，避免既有前端 / 書籤暫時壞掉；實際上都轉送到同一個處理邏輯。
@bp.route("/upload_image", methods=["POST"])
def upload_image_legacy():
    if require_login() is None:
        return jsonify({"success": False, "error": "請先登入！"}), 401

    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "沒有圖片檔案"}), 400

    if not allowed_image_file(file.filename) and not (file.mimetype or "").startswith("image/"):
        return jsonify({"success": False, "error": "不支援的圖片格式"}), 400

    original_name, safe_name = _normalized_filename(file.filename)
    unique_filename, path, stored_mime_type = store_upload(file, safe_name, "image")
    return jsonify({
        "success": True,
        "image_url": f"/uploads/{unique_filename}",
        "url": f"/uploads/{unique_filename}",
        "name": original_name[:255],
        "mime_type": stored_mime_type
    })


@bp.route("/upload_file", methods=["POST"])
def upload_file_legacy():
    if require_login() is None:
        return jsonify({"success": False, "error": "請先登入！"}), 401

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "沒有檔案"}), 400

    original_name, safe_name = _normalized_filename(file.filename)
    if not allowed_attachment_file(original_name):
        return jsonify({"success": False, "error": "不支援此檔案類型"}), 400

    unique_filename, path, stored_mime_type = store_upload(file, safe_name, "file")

    extension = safe_name.rsplit(".", 1)[1].lower() if "." in safe_name else ""
    content = read_text_file(path) if extension in ALLOWED_FILE_EXTENSIONS or safe_name.lower() in ALLOWED_SPECIAL_FILENAMES else None

    return jsonify({
        "success": True,
        "file_url": f"/uploads/{unique_filename}",
        "url": f"/uploads/{unique_filename}",
        "name": original_name[:255],
        "file_name": original_name[:255],
        "size": os.path.getsize(path),
        "mime_type": stored_mime_type,
        "content": content,
        "readable": content is not None,
        "message": None if content is not None else "此格式已上傳，但目前後端不直接解析內容。"
    })


@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    response = send_from_directory(
        current_app.config["UPLOAD_FOLDER"],
        filename,
        conditional=True,
        max_age=31536000
    )
    # 檔名含 UUID，內容更新時 URL 也會改變，因此可安全長期快取。
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
