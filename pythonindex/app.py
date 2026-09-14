"""GGPT Flask 後端入口。"""

import os
from datetime import timedelta

from flask import Flask, jsonify
from flask_limiter.errors import RateLimitExceeded
from werkzeug.middleware.proxy_fix import ProxyFix

from admin_routes import bp as admin_bp
from ai_routes import bp as ai_bp
from audit import bp as audit_bp
from auth_routes import bp as auth_bp
from config import (
    FLASK_DEBUG,
    FLASK_HOST,
    FLASK_PORT,
    FLASK_SECRET_KEY,
    MAX_UPLOAD_MB,
    SESSION_LIFETIME_HOURS,
    SESSION_COOKIE_SECURE,
    SKIP_DB_INIT,
    TRUSTED_HOSTS,
    TRUST_PROXY_COUNT,
    UPLOAD_FOLDER,
)
from conversation_routes import bp as conversations_bp
from database import ensure_schema
from global_chat_routes import bp as global_chat_bp
from traffic_routes import bp as traffic_bp
from upload_routes import bp as uploads_bp
from extensions import limiter
from security import apply_security_headers, protect_cross_site_request


def create_app():
    app = Flask(__name__)
    if TRUST_PROXY_COUNT:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=TRUST_PROXY_COUNT,
            x_proto=TRUST_PROXY_COUNT,
            x_host=TRUST_PROXY_COUNT,
        )
    app.secret_key = FLASK_SECRET_KEY
    app.config.update(
        SESSION_COOKIE_NAME="ggpt_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_LIFETIME_HOURS),
        SESSION_REFRESH_EACH_REQUEST=True,
        MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
        UPLOAD_FOLDER=UPLOAD_FOLDER,
    )
    if TRUSTED_HOSTS:
        app.config["TRUSTED_HOSTS"] = TRUSTED_HOSTS

    limiter.init_app(app)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    app.register_blueprint(audit_bp)
    app.register_blueprint(traffic_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(uploads_bp)
    app.register_blueprint(conversations_bp)
    app.register_blueprint(global_chat_bp)
    app.register_blueprint(ai_bp)

    app.before_request(protect_cross_site_request)
    app.after_request(apply_security_headers)

    @app.errorhandler(413)
    def request_entity_too_large(error):
        return jsonify({
            "success": False,
            "error": f"單次上傳請小於 {MAX_UPLOAD_MB}MB。",
        }), 413

    @app.errorhandler(RateLimitExceeded)
    def rate_limit_exceeded(error):
        return jsonify({
            "success": False,
            "error": "操作次數過多，請稍後再試。",
        }), 429

    if not SKIP_DB_INIT:
        with app.app_context():
            ensure_schema()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
