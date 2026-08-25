import os
from dotenv import load_dotenv

load_dotenv(override=True)

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

engine = None
SessionLocal = sessionmaker(autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not configured.")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    return url


def get_engine():
    global engine
    if engine is None:
        engine = create_engine(
            _database_url(),
            poolclass=NullPool,
            connect_args={"prepare_threshold": None},
        )
        SessionLocal.configure(bind=engine)
    return engine


def init_db() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=get_engine())


def get_db():
    get_engine()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
