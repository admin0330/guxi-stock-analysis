import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import config
from backend.auth import auth_store
from backend.main import app


ADMIN_PASSWORD = "StrongAdmin!2026"
USER_PASSWORD = "StrongUser!2026"


class AuthFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = auth_store.path
        auth_store.path = Path(self.temp.name) / "auth.sqlite3"
        self.patches = [
            patch.object(config, "AUTH_ENABLED", True),
            patch.object(config, "SESSION_COOKIE_SECURE", False),
            patch.object(config, "SESSION_MAX_AGE", 3600),
            patch.object(config, "SESSION_IDLE_TIMEOUT", 1800),
        ]
        for item in self.patches:
            item.start()
        auth_store.initialize()
        self.admin = auth_store.create_user("admin", ADMIN_PASSWORD, "admin")
        self.user = auth_store.create_user("reader", USER_PASSWORD, "user")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        auth_store.path = self.old_path
        self.temp.cleanup()

    def login(self, client=None, username="admin", password=ADMIN_PASSWORD):
        client = client or self.client
        response = client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf_token"]

    def test_unauthenticated_page_redirects_and_api_is_401(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/login?next="))
        self.assertEqual(self.client.get("/api/user/state").status_code, 401)
        self.assertEqual(self.client.get("/login").status_code, 200)

    def test_login_csrf_logout_and_password_is_not_plaintext(self):
        csrf = self.login()
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["username"], "admin")
        self.assertEqual(self.client.patch("/api/user/state", json={"last_page": "stock"}).status_code, 403)
        self.assertEqual(self.client.patch("/api/user/state", json={"last_page": "stock"}, headers={"X-CSRF-Token": csrf}).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code, 200)
        self.assertEqual(self.client.get("/api/user/state").status_code, 401)
        with closing(sqlite3.connect(auth_store.path)) as con:
            encoded = con.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
        self.assertTrue(encoded.startswith("scrypt$"))
        self.assertNotIn(ADMIN_PASSWORD, encoded)

    def test_admin_crud_role_protection_and_disabled_session_revocation(self):
        csrf = self.login()
        headers = {"X-CSRF-Token": csrf}
        created = self.client.post("/api/admin/users", headers=headers, json={"username": "newuser", "password": "NewUserPass!2026", "role": "user"})
        self.assertEqual(created.status_code, 201, created.text)
        target = created.json()
        self.assertEqual(self.client.patch(f"/api/admin/users/{target['id']}", headers=headers, json={"role": "admin"}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/admin/users/{target['id']}/reset-password", headers=headers, json={"password": "ResetPass!2026"}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/admin/users/{target['id']}", headers=headers).status_code, 204)
        self.assertEqual(self.client.delete(f"/api/admin/users/{self.admin['id']}", headers=headers).status_code, 400)

        user_client = TestClient(app)
        user_csrf = self.login(user_client, "reader", USER_PASSWORD)
        self.assertEqual(user_client.get("/api/admin/users").status_code, 403)
        disabled = self.client.patch(f"/api/admin/users/{self.user['id']}", headers=headers, json={"enabled": False})
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(user_client.get("/api/user/state").status_code, 401)
        self.assertEqual(user_client.post("/api/auth/logout", headers={"X-CSRF-Token": user_csrf}).status_code, 401)
        user_client.close()

    def test_login_rate_limit_is_generic(self):
        with patch.object(config, "LOGIN_MAX_FAILURES", 3), patch.object(config, "LOGIN_LOCK_SECONDS", 60):
            for _ in range(3):
                response = self.client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["detail"], "用户名或密码错误")
            limited = self.client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
            self.assertEqual(limited.status_code, 429)


if __name__ == "__main__":
    unittest.main()
