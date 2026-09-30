"""管理員與一般使用者使用不同 Cookie，避免登入、登出及多分頁更新互相覆蓋。"""

from flask import request
from flask.sessions import SecureCookieSessionInterface


class RoleSessionInterface(SecureCookieSessionInterface):
    def get_cookie_name(self, app):
        if request.path.startswith("/api/admin/") or request.path == "/admin-legacy":
            return "ggpt_admin_session"
        return super().get_cookie_name(app)
