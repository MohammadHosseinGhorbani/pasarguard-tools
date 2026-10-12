"""SQLAlchemy engine / session / Base."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    # Ensure sqlite directory exists (e.g. ./data/app.db).
    if settings.DATABASE_URL.startswith("sqlite"):
        path = settings.DATABASE_URL.split("sqlite:///")[-1].split("?")[0]
        if path and path != ":memory:":
            import os

            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
    Base.metadata.create_all(bind=engine)

    # Lightweight migration: add servers.role on existing databases.
    from sqlalchemy import text

    with engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(servers)")).fetchall()]
        if "role" not in cols:
            conn.execute(text("ALTER TABLE servers ADD COLUMN role VARCHAR(16) DEFAULT 'kharej'"))
            conn.execute(text("UPDATE servers SET role='kharej' WHERE role IS NULL OR role=''"))
        for col, default in (
            ("bp_url", "''"),
            ("bp_token", "''"),
            ("bp_node", "''"),
        ):
            if col not in [row[1] for row in conn.execute(text("PRAGMA table_info(servers)")).fetchall()]:
                conn.execute(text(f"ALTER TABLE servers ADD COLUMN {col} VARCHAR(512) DEFAULT {default}"))
        if "bp_verify" not in [row[1] for row in conn.execute(text("PRAGMA table_info(servers)")).fetchall()]:
            conn.execute(text("ALTER TABLE servers ADD COLUMN bp_verify BOOLEAN DEFAULT 0"))
        conn.execute(
            text("UPDATE servers SET role='kharej' WHERE role NOT IN ('iran','kharej')")
        )

    # Seed single admin user.
    from passlib.context import CryptContext

    from .models import User

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == settings.ADMIN_USER).first()
        if existing is None:
            db.add(
                User(
                    username=settings.ADMIN_USER,
                    password_hash=pwd.hash(settings.ADMIN_PASS),
                    is_admin=True,
                )
            )
            db.commit()
    finally:
        db.close()
