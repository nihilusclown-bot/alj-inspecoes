import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from urllib.parse import quote_plus

PROJECT_REF = "viqwzdcrmutgfvebszrg"


def _get_database_url() -> str:
    if "DATABASE_URL" in st.secrets and st.secrets["DATABASE_URL"]:
        return st.secrets["DATABASE_URL"]

    password = st.secrets["DB_PASSWORD"]
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