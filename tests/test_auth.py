import uuid
import unittest

from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.main import app
from app.models import User


class AuthFlowTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.client = TestClient(app)
        self.email = "nt-test-%s@example.com" % uuid.uuid4().hex[:10]
        self.password = "testpass123"

    def tearDown(self):
        db = SessionLocal()
        try:
            db.query(User).filter(User.email == self.email).delete()
            db.commit()
        finally:
            db.close()

    def test_register_login_me_logout(self):
        created = self.client.post(
            "/api/auth/register",
            json={"email": self.email, "password": self.password},
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["email"], self.email)

        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["email"], self.email)

        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

        bad = self.client.post(
            "/api/auth/login",
            json={"email": self.email, "password": "wrongpass"},
        )
        self.assertEqual(bad.status_code, 401)

        login = self.client.post(
            "/api/auth/login",
            json={"email": self.email, "password": self.password},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

        levels = self.client.get("/api/levels/AAPL")
        self.assertNotEqual(levels.status_code, 401)

    def test_home_includes_login_and_register(self):
        html = self.client.get("/").text
        self.assertIn("loginForm", html)
        self.assertIn("registerForm", html)
        self.assertIn("Seleccionar o escribir ticker", html)


class DatabaseUrlTests(unittest.TestCase):
    def test_database_url_strips_channel_binding(self):
        from unittest.mock import patch
        from app.db import _database_url
        raw = "postgresql://u:p@host/db?sslmode=require&channel_binding=require"
        with patch.dict("os.environ", {"DATABASE_URL": raw}, clear=False):
            normalized = _database_url()
        self.assertIn("postgresql+psycopg://", normalized)
        self.assertNotIn("channel_binding", normalized)
        self.assertIn("sslmode=require", normalized)

    def test_postgres_url_alias_is_accepted(self):
        from unittest.mock import patch
        from app.db import _database_url
        raw = "postgres://u:p@host/db?sslmode=require"
        env = {"DATABASE_URL": "", "POSTGRES_URL": raw}
        with patch.dict("os.environ", env, clear=False):
            normalized = _database_url()
        self.assertTrue(normalized.startswith("postgresql+psycopg://"))


if __name__ == "__main__":
    unittest.main()
