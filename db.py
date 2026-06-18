import os

import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from urllib.parse import quote_plus

PROJECT_REF = "viqwzdcrmutgfvebszrg"
CONNECTION_NAME = "supabase"


def _has_streamlit_connection() -> bool:
    try:
        return "connections" in st.secrets and CONNECTION_NAME in st.secrets.connections
    except Exception:
        return False


def _lookup_secret(*keys, default=None):
    for key in keys:
        try:
            if key in st.secrets and st.secrets[key]:
                return st.secrets[key]
        except Exception:
            pass

        env_value = os.getenv(key)
        if env_value:
            return env_value

    return default


def _show_secrets_help() -> None:
    st.error("Database secrets not configured on Streamlit Cloud.")
    st.markdown("1. Open **Manage app** → **Settings** → **Secrets**")
    st.markdown("2. **Delete everything** in the text box")
    st.markdown("3. Paste **exactly** the text below (each line separate, no markdown):")
    st.code(
        """[connections.supabase]
dialect = "postgresql"
host = "aws-1-us-east-1.pooler.supabase.com"
port = 5432
database = "postgres"
username = "postgres.viqwzdcrmutgfvebszrg"
password = "0664f32de30A@"

""",
        language="toml",
    )
    st.markdown("4. Click **Save**, then **Reboot app** (⋮ menu → Reboot app)")


def _normalize_database_url(database_url: str) -> str:
    database_url = database_url.strip()
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif database_url.startswith("postgresql://") and "+psycopg2" not in database_url:
        database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return database_url


def _get_database_url() -> str:
    database_url = _lookup_secret("DATABASE_URL")
    if database_url:
        return _normalize_database_url(database_url)

    password = _lookup_secret("DB_PASSWORD")
    if not password:
        _show_secrets_help()
        st.stop()

    host = _lookup_secret("DB_HOST", default="aws-1-us-east-1.pooler.supabase.com")
    port = _lookup_secret("DB_PORT", default=5432)
    user = _lookup_secret("DB_USER", default=f"postgres.{PROJECT_REF}")
    dbname = _lookup_secret("DB_NAME", default="postgres")
    return f"postgresql+psycopg2://{user}:{quote_plus(str(password))}@{host}:{port}/{dbname}"


@st.cache_resource
def _get_fallback_engine():
    return create_engine(
        _get_database_url(),
        poolclass=NullPool,
        pool_pre_ping=True,
    )


def get_engine():
    if _has_streamlit_connection():
        return st.connection(CONNECTION_NAME, type="sql")._instance
    return _get_fallback_engine()


def read_sql(query: str, params=None) -> pd.DataFrame:
    return pd.read_sql(query, get_engine(), params=params)


def execute(query: str, params=None) -> None:
    with get_engine().begin() as conn:
        conn.execute(text(query), params or {})