"""One-time setup: create tables and seed the admin user."""

import os
import sys
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

PROJECT_REF = "viqwzdcrmutgfvebszrg"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    nome          TEXT UNIQUE NOT NULL,
    email         TEXT,
    senha         TEXT NOT NULL,
    funcao        TEXT,
    funcao_custom TEXT
);

CREATE TABLE IF NOT EXISTS pecas (
    qr_code               TEXT PRIMARY KEY,
    tipo_peca             TEXT,
    cor_atual             TEXT,
    status                TEXT,
    etapa                 TEXT,
    responsavel           TEXT,
    cadastrado_por        TEXT,
    data_cadastro         TEXT,
    data_atualizacao      TEXT,
    resultado             TEXT,
    data_conclusao        TEXT,
    responsavel_conclusao TEXT,
    desenho_tecnico       BYTEA
);

CREATE TABLE IF NOT EXISTS historico (
    id          SERIAL PRIMARY KEY,
    qr_code     TEXT REFERENCES pecas(qr_code) ON DELETE CASCADE,
    tipo_peca   TEXT,
    etapa       TEXT,
    cor         TEXT,
    status      TEXT,
    responsavel TEXT,
    data        TEXT,
    observacao  TEXT
);

CREATE INDEX IF NOT EXISTS idx_historico_qr_code ON historico(qr_code);
CREATE INDEX IF NOT EXISTS idx_pecas_resultado ON pecas(resultado);
"""

MIGRATION_SQL = """
ALTER TABLE pecas ADD COLUMN IF NOT EXISTS data_atualizacao TEXT;
UPDATE pecas SET data_atualizacao = data_cadastro
WHERE data_atualizacao IS NULL AND data_cadastro IS NOT NULL;
"""

SEED_ADMIN_SQL = """
DELETE FROM users WHERE nome != 'admin';

INSERT INTO users (nome, email, senha, funcao, funcao_custom)
VALUES ('admin', NULL, 'mec447', 'Administrador', NULL)
ON CONFLICT (nome) DO UPDATE SET
    senha = EXCLUDED.senha,
    funcao = EXCLUDED.funcao,
    email = EXCLUDED.email,
    funcao_custom = EXCLUDED.funcao_custom;
"""


def _load_secrets() -> dict:
    secrets_path = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
    if not os.path.exists(secrets_path):
        return {}

    try:
        import tomllib
        with open(secrets_path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        import toml
        with open(secrets_path, "r", encoding="utf-8") as f:
            return toml.load(f)


def _get_database_url() -> str:
    secrets = _load_secrets()

    url = os.getenv("DATABASE_URL") or secrets.get("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg2://", 1)
        elif url.startswith("postgresql://") and "+psycopg2" not in url:
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url

    password = os.getenv("DB_PASSWORD") or secrets.get("DB_PASSWORD")
    if not password or password == "PASTE_YOUR_DATABASE_PASSWORD_HERE":
        print("Error: set DB_PASSWORD in .streamlit/secrets.toml or as an environment variable.")
        sys.exit(1)

    host = os.getenv("DB_HOST") or secrets.get("DB_HOST", "aws-1-us-east-1.pooler.supabase.com")
    port = os.getenv("DB_PORT") or secrets.get("DB_PORT", "5432")
    user = os.getenv("DB_USER") or secrets.get("DB_USER", f"postgres.{PROJECT_REF}")
    dbname = os.getenv("DB_NAME") or secrets.get("DB_NAME", "postgres")
    return f"postgresql+psycopg2://{user}:{quote_plus(password)}@{host}:{port}/{dbname}"


def main():
    engine = create_engine(_get_database_url(), poolclass=NullPool, pool_pre_ping=True)
    with engine.begin() as conn:
        for statement in SCHEMA_SQL.strip().split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))
        for statement in MIGRATION_SQL.strip().split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))
        conn.execute(text(SEED_ADMIN_SQL))

    print("Tables created and admin user seeded successfully.")


if __name__ == "__main__":
    main()