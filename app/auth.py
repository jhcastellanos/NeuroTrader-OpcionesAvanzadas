import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

COOKIE_NAME = "nt_session"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
router = APIRouter(prefix="/api/auth", tags=["auth"])
cookie_scheme = APIKeyCookie(name=COOKIE_NAME, auto_error=False)


def _jwt_secret() -> str:
    secret = (os.getenv("JWT_SECRET") or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticación no configurada.",
        )
    return secret


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def _cookie_secure() -> bool:
    flag = (os.getenv("COOKIE_SECURE") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    if (os.getenv("VERCEL") or "").strip() == "1":
        return True
    env = (os.getenv("RAILWAY_ENVIRONMENT") or "").strip().lower()
    return env in {"production", "prod"}


def set_session(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        samesite="lax",
        secure=_cookie_secure(),
    )


class AuthIn(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not EMAIL_RE.fullmatch(email):
            raise ValueError("Email inválido.")
        return email


class UserOut(BaseModel):
    id: int
    email: str


def get_current_user(
    token: Optional[str] = Depends(cookie_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar.")
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
        user_id = int(payload.get("sub") or 0)
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada.")
    return user


@router.post("/register", response_model=UserOut)
def register(body: AuthIn, response: Response, db: Session = Depends(get_db)):
    _jwt_secret()
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ese email ya está registrado.")
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No fue posible conectar con la base de datos.",
        ) from exc
    db.refresh(user)
    set_session(response, create_token(user.id, user.email))
    return user


@router.post("/login", response_model=UserOut)
def login(body: AuthIn, response: Response, db: Session = Depends(get_db)):
    _jwt_secret()
    try:
        user = db.query(User).filter(User.email == body.email).one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No fue posible conectar con la base de datos.",
        ) from exc
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email o contraseña incorrectos.")
    set_session(response, create_token(user.id, user.email))
    return user


@router.post("/logout")
def logout(response: Response):
    clear_session(response)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/health")
def auth_health():
    from sqlalchemy import text

    from .db import get_engine

    db_names = (
        "DATABASE_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "DATABASE_URL_UNPOOLED",
        "NEON_DATABASE_URL",
        "PGHOST",
        "POSTGRES_HOST",
    )
    configured = any(bool((os.getenv(name) or "").strip()) for name in db_names)
    connected = False
    if configured:
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            connected = True
        except Exception:
            connected = False
    return {
        "database_configured": configured,
        "database_connected": connected,
        "jwt_configured": bool((os.getenv("JWT_SECRET") or "").strip()),
    }
