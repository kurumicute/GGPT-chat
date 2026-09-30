"""Regression checks without database writes or paid API requests.

Run: python -m unittest discover -s tests -p test_backend.py -v
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pythonindex"))

from flask import Flask
import admin_routes
import auth_routes
import ai_routes
from config import MODEL_PRICING
from extensions import limiter
from pricing import calculate_model_cost
from role_sessions import RoleSessionInterface


def connection(row):
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = row
    return conn


class SessionIsolationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test-only-session-secret"
        self.app.config.update(TESTING=True, RATELIMIT_ENABLED=False,
                               SESSION_COOKIE_NAME="ggpt_session")
        self.app.session_interface = RoleSessionInterface()
        limiter.init_app(self.app)
        self.app.register_blueprint(auth_routes.bp)
        self.app.register_blueprint(admin_routes.bp)
        self.client = self.app.test_client()
        user = {"id": 11, "username": "tester", "password_hash": "test-hash"}
        admin = {"id": 21, "username": "owner", "password_hash": "test-hash", "is_active": 1}
        for target, value in [
            ("auth_routes.get_conn", connection(user)),
            ("admin_routes.get_conn", connection(admin)),
        ]:
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        for target in ["auth_routes.constant_time_password_check", "admin_routes.constant_time_password_check"]:
            mock = patch(target, side_effect=lambda stored, password: password == "valid-password")
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch("auth_routes.ensure_user_has_conversation", return_value=1)
        mock.start()
        self.addCleanup(mock.stop)

    def login_user(self):
        return self.client.post("/login", json={"username": "tester", "password": "valid-password"})

    def login_admin(self):
        return self.client.post("/api/admin/login", json={"username": "owner", "password": "valid-password"})

    def assert_sessions(self, user, admin):
        self.assertEqual(self.client.get("/check_session").json["logged_in"], user)
        self.assertEqual(self.client.get("/api/admin/check").json["logged_in"], admin)

    def test_admin_login_preserves_user(self):
        self.assertEqual(self.login_user().status_code, 200)
        self.assertEqual(self.login_admin().status_code, 200)
        self.assert_sessions(True, True)
        self.assertIsNotNone(self.client.get_cookie("ggpt_session"))
        self.assertIsNotNone(self.client.get_cookie("ggpt_admin_session"))

    def test_user_login_preserves_admin(self):
        self.login_admin()
        self.login_user()
        self.assert_sessions(True, True)

    def test_admin_logout_preserves_user(self):
        self.login_user()
        self.login_admin()
        self.client.post("/api/admin/logout")
        self.assert_sessions(True, False)

    def test_user_logout_preserves_admin(self):
        self.login_admin()
        self.login_user()
        self.client.post("/logout")
        self.assert_sessions(False, True)

    def test_failed_admin_login_preserves_user(self):
        self.login_user()
        result = self.client.post("/api/admin/login", json={"username": "owner", "password": "wrong"})
        self.assertEqual(result.status_code, 401)
        self.assert_sessions(True, False)

    def test_user_cookie_does_not_grant_admin_access(self):
        self.login_user()
        self.assertEqual(self.client.get("/api/admin/dashboard").status_code, 403)


class ModelTests(unittest.TestCase):
    def test_catalog_and_prices(self):
        self.assertNotIn("gpt-5-nano", MODEL_PRICING)
        for name, cached, input_cost, output in [
            ("gpt-6-luna", .01, .10, .50),
            ("gpt-6-sol", .20, 2, 10),
            ("gpt-6.1-sol", .10, 2, 10),
        ]:
            with self.subTest(model=name):
                self.assertEqual(MODEL_PRICING[name], {"input": input_cost, "cached_input": cached, "output": output})
                self.assertAlmostEqual(calculate_model_cost(name, 1_000_000, 1_000_000, 500_000), input_cost / 2 + cached / 2 + output)

    def test_removed_model_is_rejected(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        limiter.init_app(app)
        app.register_blueprint(ai_routes.bp)
        with patch("ai_routes.require_login", return_value=11):
            result = app.test_client().post("/chat", json={"model": "gpt-5-nano", "message": "Hello"})
        self.assertEqual(result.status_code, 400)
        self.assertIn("不支援的模型", result.json["error"])

    def test_new_models_pass_validation(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        limiter.init_app(app)
        app.register_blueprint(ai_routes.bp)
        for name in ["gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol"]:
            with self.subTest(model=name), patch("ai_routes.require_login", return_value=11), \
                    patch("ai_routes.get_conn", return_value=connection(None)), \
                    patch("ai_routes.accessible_conversation", return_value=None):
                result = app.test_client().post("/chat", json={"model": name, "message": "Hello", "conversation_id": 1})
                self.assertEqual(result.status_code, 404)

    def test_new_model_requests_and_reasoning_modes(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        limiter.init_app(app)
        app.register_blueprint(ai_routes.bp)
        response = SimpleNamespace(output_text="測試回答", output=[], usage=SimpleNamespace(
            input_tokens=1000, output_tokens=500, total_tokens=1500,
            input_tokens_details=SimpleNamespace(cached_tokens=200),
            output_tokens_details=SimpleNamespace(reasoning_tokens=100),
        ))
        for name, enabled, expected in [
            ("gpt-6-luna", False, "none"),
            ("gpt-6-sol", True, "high"),
            ("gpt-6.1-sol", False, "high"),
        ]:
            conn = connection({"c": 1})
            conn.cursor.return_value.fetchall.return_value = []
            with self.subTest(model=name), patch("ai_routes.require_login", return_value=11), \
                    patch("ai_routes.get_conn", return_value=conn), \
                    patch("ai_routes.accessible_conversation", return_value={"title": "測試", "user_id": 11, "is_owner": 1}), \
                    patch.object(ai_routes.client.responses, "create", return_value=response) as create:
                result = app.test_client().post("/chat", json={"model": name, "message": "Hello", "conversation_id": 1,
                                                                  "reasoning_enabled": enabled, "reasoning_effort": "high"})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(create.call_args.kwargs["model"], name)
                self.assertEqual(create.call_args.kwargs["reasoning"]["effort"], expected)
                self.assertEqual(result.json["usage"]["reasoning_enabled"], expected != "none")
                self.assertAlmostEqual(result.json["usage"]["total_cost"], calculate_model_cost(name, 1000, 500, 200))


if __name__ == "__main__":
    unittest.main()
