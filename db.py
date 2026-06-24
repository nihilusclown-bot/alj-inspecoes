import os

import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from urllib.parse import quote_plus

PROJECT_REF = "viqwzdcrmutgfvebszrg"
CONNECTION_NAME = "supabase"
CACHE_TTL = 60

# Exclude desenho_tecnico (BYTEA) — not serializable by @st.cache_data
PECAS_COLS = """
    qr_code, tipo_peca, cor_atual, status, etapa, responsavel, cadastrado_por,
    data_cadastro, resultado, data_conclusao, responsavel_conclusao
"""


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
password = "mec447alj@teste"

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


def _read_sql_uncached(query: str, params=None) -> pd.DataFrame:
    return pd.read_sql(query, get_engine(), params=params)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _read_sql_cached(query: str, params: tuple = ()) -> pd.DataFrame:
    params_dict = dict(params) if params else None
    return _read_sql_uncached(query, params_dict)


def read_sql(query: str, params=None, *, use_cache: bool = True) -> pd.DataFrame:
    if not use_cache:
        return _read_sql_uncached(query, params)
    param_tuple = tuple(sorted(params.items())) if params else ()
    return _read_sql_cached(query, param_tuple)


def clear_query_cache() -> None:
    _read_sql_cached.clear()
    load_pecas_ativas_full.clear()
    load_pecas_ativas_listagem.clear()
    load_pecas_ativas_dropdown.clear()
    load_pecas_concluidas_full.clear()
    load_pecas_concluidas_resumo.clear()
    load_peca_by_qr.clear()
    load_historico_by_qr.clear()
    load_historico_publico_by_qr.clear()
    load_produtividade_historico.clear()
    load_gerenciar_pecas.clear()
    load_users.clear()
    load_operadores.clear()
    load_total_pecas_count.clear()


def execute(query: str, params=None) -> None:
    with get_engine().begin() as conn:
        conn.execute(text(query), params or {})
    clear_query_cache()


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_pecas_ativas_full() -> pd.DataFrame:
    return _read_sql_uncached(
        f"SELECT {PECAS_COLS} FROM pecas WHERE resultado IS NULL OR resultado = ''"
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_pecas_ativas_listagem() -> pd.DataFrame:
    return _read_sql_uncached("""
        SELECT qr_code, tipo_peca, etapa, status, responsavel, data_cadastro
        FROM pecas
        WHERE resultado IS NULL OR resultado = ''
    """)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_pecas_ativas_dropdown() -> pd.DataFrame:
    return _read_sql_uncached("""
        SELECT qr_code, tipo_peca
        FROM pecas
        WHERE resultado IS NULL OR resultado = ''
        ORDER BY data_cadastro DESC
    """)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_pecas_concluidas_full() -> pd.DataFrame:
    return _read_sql_uncached(
        f"SELECT {PECAS_COLS} FROM pecas WHERE resultado IS NOT NULL"
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_pecas_concluidas_resumo() -> pd.DataFrame:
    return _read_sql_uncached("""
        SELECT qr_code, tipo_peca
        FROM pecas
        WHERE resultado IS NOT NULL
    """)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_peca_by_qr(qr: str) -> pd.DataFrame:
    return _read_sql_uncached(
        f"SELECT {PECAS_COLS} FROM pecas WHERE qr_code = %(qr)s",
        params={"qr": qr},
    )


def normalize_bytea(value) -> bytes | None:
    """Convert PostgreSQL BYTEA (often memoryview) to bytes for Streamlit widgets."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        if value.startswith("\\x"):
            return bytes.fromhex(value[2:])
        return value.encode()
    return bytes(value)


def load_desenho_tecnico_by_qr(qr: str) -> bytes | None:
    """Uncached — BYTEA cannot be stored in @st.cache_data."""
    df = _read_sql_uncached(
        "SELECT desenho_tecnico FROM pecas WHERE qr_code = %(qr)s",
        params={"qr": qr},
    )
    if df.empty:
        return None
    data = normalize_bytea(df.iloc[0]["desenho_tecnico"])
    return data if data else None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_historico_by_qr(qr: str) -> pd.DataFrame:
    return _read_sql_uncached("""
        SELECT
            tipo_peca AS "Tipo da Peça",
            etapa     AS "Etapa",
            status    AS "Status",
            responsavel AS "Responsável",
            data      AS "Data/Hora",
            observacao AS "Observação"
        FROM historico
        WHERE qr_code = %(qr)s
        ORDER BY data ASC
    """, params={"qr": qr})


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_historico_publico_by_qr(qr: str) -> pd.DataFrame:
    return _read_sql_uncached("""
        SELECT data AS "Data/Hora",
               responsavel AS "Responsável",
               etapa AS "Etapa",
               status AS "Status",
               observacao AS "Comentário"
        FROM historico
        WHERE qr_code = %(qr)s
        ORDER BY data ASC
    """, params={"qr": qr})


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_produtividade_historico() -> pd.DataFrame:
    return _read_sql_uncached("""
        SELECT h.*, p.etapa as etapa_atual,
               SUBSTRING(h.data FROM 7 FOR 4) || '-' || SUBSTRING(h.data FROM 4 FOR 2) as mes
        FROM historico h
        LEFT JOIN pecas p ON h.qr_code = p.qr_code
    """)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_gerenciar_pecas() -> pd.DataFrame:
    return _read_sql_uncached("""
        SELECT
            qr_code AS "QR Code",
            tipo_peca AS "Tipo da Peça",
            etapa AS "Etapa",
            status AS "Status",
            responsavel AS "Responsável",
            data_cadastro AS "Data Cadastro"
        FROM pecas
        WHERE resultado IS NULL OR resultado = ''
        ORDER BY data_cadastro DESC
    """)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_users() -> pd.DataFrame:
    return _read_sql_uncached("SELECT id, nome, funcao, funcao_custom FROM users")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_operadores() -> pd.DataFrame:
    return _read_sql_uncached("SELECT funcao, nome FROM users WHERE funcao = 'Operador'")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def load_total_pecas_count() -> int:
    df = _read_sql_uncached("SELECT qr_code FROM pecas")
    return len(df)