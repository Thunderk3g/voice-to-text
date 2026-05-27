"""
SQLAlchemy declarative base + naming convention.

A single shared `Base` is imported by ORM models in `app/db/models.py` and by
Alembic's env.py for autogeneration. The explicit naming convention keeps
generated migration names deterministic across environments.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Stable naming convention -> reproducible Alembic constraint names.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata_obj = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base used by every ORM model in the project."""

    metadata = metadata_obj
