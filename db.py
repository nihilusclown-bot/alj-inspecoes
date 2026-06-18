import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from urllib.parse import quote_plus

PROJECT_REF = "viqwzdcrmutgfvebszrg"


def _missing_secrets_message() -> str:
    return """
**Database secrets not configured.**

Add these to **Streamlit Cloud → Settings → Secrets** (or `.streamlit/secrets.toml` locally):

```toml
DB_PASSWORD = "your-supabase-database-password"
DB_HOST = "aws-1-us-east-1.pooler.supabase.com"
DB_PORT = 5432
DB_USER = "postgres.viqwzdcrmutgfvebszrg"
DB_NAME = "postgres"
```

Or use a single connection string:

```toml
DATABASE_URL = "postgresql://postgres.viqwzdcrmutgfvebszrg:YOUR_PASSWORD@aws-1-us-east-1.pooler.supabase.com:5432/postgres"
```
"""


def _get_database_url() -> str:
    database_url = st.secrets.get("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
        elif database_url.startswith("postgresql://") and "+psycopg2" not in database_url:
            database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return database_url

    password = st.secrets.get("DB_PASSWORD")
    if not password:
        st.error(_missing_secrets_message())
        st.stop()

    host = st.secrets.get("DB_HOST", "aws-1-us-east-1.pooler.supabase.com")
    port = st.secrets.get("DB_PORT", 5432)
    user = st.secrets.get("DB_USER", f"postgres.{PROJECT_REF}")
    dbname = st.secrets.get("DB_NAME", "postgres")
    return f"postgresql+psycopg2://{user}:{quote_plus(password)}@{host}:{port}/{dbname}"


@st.cache_resource
def get_engine():
    return create_engine(
        _get_database_url(),
        poolclass=NullPool,
        pool_pre_ping=True,
    )


def read_sql(query: str, params=None) -> pd.DataFrame:
    return pd.read_sql(query, get_engine(), params=params)


def execute(query: str, params=None) -> None:
    with get_engine().begin() as conn:
        conn.execute(text(query), params or {})