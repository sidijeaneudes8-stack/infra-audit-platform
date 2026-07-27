"""
Connexion PostgreSQL Serverless (Supabase / Neon.tech) via SQLAlchemy.
Conçu pour un contexte Vercel Serverless : connexions courtes, fermées
explicitement à la fin de chaque invocation (NullPool recommandé côté
Supabase/Neon pooler en mode "transaction").
"""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from models.schema import Base

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL manquant. Définis-le dans les variables d'environnement Vercel."
    )

# NullPool : chaque invocation serverless ouvre/ferme sa propre connexion.
# Utilise de préférence l'URL du "pooler" Supabase (port 6543, pgbouncer) ou
# le pooler Neon pour éviter d'épuiser les connexions Postgres.
engine = create_engine(DATABASE_URL, poolclass=NullPool, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Crée les tables si elles n'existent pas encore. À lancer une fois manuellement
    (ou via une migration Alembic en production)."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session():
    """Context manager garantissant la fermeture de la session/connexion,
    indispensable en environnement serverless (Vercel coupe le process après la réponse)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()
