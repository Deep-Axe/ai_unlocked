"""
ChainFlow — database.py
SQLAlchemy engine and session factory.

Supports Azure SQL via pyodbc. The DATABASE_URL environment variable
must be set before importing this module.

Connection pool settings are tuned for Azure SQL Basic tier (5 DTUs):
  pool_size=3        — keep 3 persistent connections
  max_overflow=5     — allow up to 5 extra under load
  pool_timeout=30    — wait up to 30s for a connection
  pool_pre_ping=True — test connections before using (handles Azure
                       idle disconnects after 30 min)
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

# Azure SQL via pyodbc — no SQLite-specific args needed
engine = create_engine(
    DATABASE_URL,
    pool_size=3,
    max_overflow=5,
    pool_timeout=30,
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency — yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> bool:
    """Returns True if the database is reachable, False otherwise."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
