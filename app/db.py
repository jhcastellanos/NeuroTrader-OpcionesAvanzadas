import logging
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from fastapi import HTTPException, status
from sqlalchemy import create_engine
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
    url = (os.getenv("DATABASE_URL") or "").strip()
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
        if lowered == "channel_binding":
            continue
        if lowered == "sslmode":
            has_ssl = True
        query.append((key, value))
    if not has_ssl:
        query.append(("sslmode", "require"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def get_engine():
    global engine
    if engine is None:
        try:
            engine = create_engine(
                _database_url(),
                poolclass=NullPool,
                connect_args={"prepare_threshold": None, "connect_timeout": 10},
            )
        except Exception as exc:
            logger.exception("Could not create database engine")
            raise DatabaseUnavailable(str(exc) or "database engine failed") from exc
        SessionLocal.configure(bind=engine)
        try:
            from . import models  # noqa: F401
            Base.metadata.create_all(bind=engine)
        except Exception:
            logger.exception("Could not initialize database tables")
    return engine


def reset_engine():
    global engine
    engine = None


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
