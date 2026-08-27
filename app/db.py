import logging
import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from fastapi import HTTPException, status
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

load_dotenv(override=True)

logger = logging.getLogger("neurotrader.db")

engine = None
SessionLocal = sessionmaker(autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class DatabaseUnavailable(RuntimeError):
    pass


def _database_url() -> str:
    url = ""
    for name in (
        "DATABASE_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "DATABASE_URL_UNPOOLED",
        "NEON_DATABASE_URL",
    ):
        url = (os.getenv(name) or "").strip()
        if url:
            break
    if not url:
        host = (os.getenv("PGHOST") or os.getenv("POSTGRES_HOST") or "").strip()
        user = (os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or "").strip()
        password = (os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or "").strip()
        database = (os.getenv("PGDATABASE") or os.getenv("POSTGRES_DATABASE") or os.getenv("POSTGRES_DB") or "").strip()
        if host and user and database:
            url = "postgresql://%s:%s@%s/%s" % (user, password, host, database)
    if not url:
        raise DatabaseUnavailable("DATABASE_URL is not configured.")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    parts = urlsplit(url)
    query = []
    has_ssl = False
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in {"channel_binding", "pgbouncer"}:
            continue
        if lowered == "sslmode":
            has_ssl = True
        query.append((key, value))
    if not has_ssl:
        query.append(("sslmode", "require"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def get_engine():
    global engine
    if engine is not None:
        return engine
    last_error = None
    for attempt in range(1, 4):
        bind = None
        try:
            bind = create_engine(
                _database_url(),
                poolclass=NullPool,
                connect_args={"prepare_threshold": None, "connect_timeout": 15},
            )
            SessionLocal.configure(bind=bind)
            from . import models  # noqa: F401
            Base.metadata.create_all(bind=bind)
            with bind.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine = bind
            return engine
        except Exception as exc:
            last_error = exc
            logger.exception("Database init failed (attempt %s/3)", attempt)
            if bind is not None:
                bind.dispose()
            SessionLocal.configure(bind=None)
            time.sleep(0.6 * attempt)
    engine = None
    raise DatabaseUnavailable(str(last_error) or "database init failed") from last_error


def reset_engine():
    global engine
    if engine is not None:
        engine.dispose()
    engine = None
    SessionLocal.configure(bind=None)


def init_db() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=get_engine())


def get_db():
    try:
        get_engine()
    except DatabaseUnavailable as exc:
        logger.exception("Database unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No fue posible conectar con la base de datos.",
        ) from exc
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
