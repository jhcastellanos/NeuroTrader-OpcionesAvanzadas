import unittest

from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app


class OpenAccessTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_home_has_no_login(self):
        html = self.client.get("/").text
        self.assertNotIn("loginForm", html)
        self.assertNotIn("registerForm", html)
        self.assertNotIn("Inicia sesión", html)
        self.assertIn("Seleccionar o escribir ticker", html)
        self.assertIn('id="workspace"', html)

    def test_levels_does_not_require_auth(self):
        r = self.client.get("/api/levels/AAPL")
        self.assertNotEqual(r.status_code, 401)
        self.assertNotEqual(r.status_code, 403)

    def test_auth_routes_are_gone(self):
        self.assertEqual(self.client.get("/api/auth/me").status_code, 404)
        self.assertEqual(self.client.post("/api/auth/login", json={"email": "a@b.com", "password": "password"}).status_code, 404)


class DatabaseUrlTests(unittest.TestCase):
    def test_database_url_strips_channel_binding(self):
        from app.db import _database_url
        raw = "postgresql://u:p@host/db?sslmode=require&channel_binding=require"
        with patch.dict("os.environ", {"DATABASE_URL": raw}, clear=False):
            normalized = _database_url()
        self.assertIn("postgresql+psycopg://", normalized)
        self.assertNotIn("channel_binding", normalized)
        self.assertIn("sslmode=require", normalized)

    def test_postgres_url_alias_is_accepted(self):
        from app.db import _database_url
        raw = "postgres://u:p@host/db?sslmode=require"
        env = {"DATABASE_URL": "", "POSTGRES_URL": raw}
        with patch.dict("os.environ", env, clear=False):
            normalized = _database_url()
        self.assertTrue(normalized.startswith("postgresql+psycopg://"))


if __name__ == "__main__":
    unittest.main()
