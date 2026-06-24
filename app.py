import streamlit as st
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import qrcode
from PIL import Image, ImageDraw, ImageFont
import io
import altair as alt
from sqlalchemy.exc import IntegrityError

from db import (
    read_sql,
    execute,
    load_pecas_ativas_full,
    load_pecas_ativas_listagem,
    load_pecas_ativas_dropdown,
    load_pecas_concluidas_full,
    load_pecas_concluidas_resumo,
    load_peca_by_qr,
    fetch_peca_by_qr,
    load_historico_by_qr,
    fetch_historico_publico_by_qr,
    load_produtividade_historico,
    load_gerenciar_pecas,
    load_users,
    load_operadores,
    load_desenho_tecnico_by_qr,
    infer_desenho_mime,
)

def _parse_data_br(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="%d/%m/%Y %H:%M", errors="coerce")


def _filtrar_pecas_por_mes(df: pd.DataFrame, mes_ref: str) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    data_ref = df["data_conclusao"].fillna(df["data_cadastro"])
    df["_mes"] = _parse_data_br(data_ref).dt.strftime("%Y-%m")
    return df[df["_mes"] == mes_ref].drop(columns="_mes")


def _safe_pct_round(numerator: pd.Series, denominator: pd.Series, decimals: int = 1) -> pd.Series:
    num = pd.to_numeric(numerator, errors="coerce").fillna(0.0)
    den = pd.to_numeric(denominator, errors="coerce").fillna(0.0)
    pct = num.div(den.where(den != 0)).mul(100)
    return pct.replace([float("inf"), float("-inf")], 0.0).fillna(0.0).round(decimals)


MAX_DESENHO_BYTES = 10 * 1024 * 1024


def _peca_data_atualizacao(peca) -> str:
    return peca.get("data_atualizacao") or peca.get("data_conclusao") or peca.get("data_cadastro", "—")


def _pecas_unicas_periodo(df_filtrado: pd.DataFrame) -> pd.DataFrame:
    if df_filtrado.empty:
        return df_filtrado
    return df_filtrado.sort_values("data").drop_duplicates("qr_code", keep="last")


def _etiqueta_pdf_bytes(img) -> bytes | None:
    try:
        buf = io.BytesIO()
        img.save(buf, format="PDF", resolution=300)
        return buf.getvalue()
    except Exception:
        return None


def _render_etiqueta_download(img, qr: str, label: str, *, primary: bool = False) -> bytes:
    pdf_bytes = _etiqueta_pdf_bytes(img)
    if pdf_bytes:
        st.download_button(
            label=label,
            data=pdf_bytes,
            file_name=f"etiqueta_{qr}.pdf",
            mime="application/pdf",
            type="primary" if primary else "secondary",
            use_container_width=True,
        )
        return pdf_bytes

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    st.warning("PDF indisponível neste servidor. Baixando como imagem PNG.")
    png_bytes = buf.getvalue()
    st.download_button(
        label=label,
        data=png_bytes,
        file_name=f"etiqueta_{qr}.png",
        mime="image/png",
        type="primary" if primary else "secondary",
        use_container_width=True,
    )
    return png_bytes


st.set_page_config(page_title="ALJ Inspeções", layout="wide")
# ==================== PÁGINA PÚBLICA VIA QR CODE ====================
query_params = st.query_params
if "qr_code" in query_params:
    qr = query_params["qr_code"]
    if isinstance(qr, list):
        qr = qr[0]
    qr = str(qr).strip()

    if not qr:
        st.error("❌ Código QR inválido.")
        st.stop()

    df = fetch_peca_by_qr(qr)
    if not df.empty:
        peca = df.iloc[0]

        st.title(f"📋 Peça: **{qr}**")
        st.subheader(peca["tipo_peca"])

        st.write(f"**Etapa atual:** {peca.get('etapa', '—')}")
        st.write(f"**Responsável:** {peca.get('responsavel', '—')}")
        st.write(f"**Data de cadastro:** {peca.get('data_cadastro', '—')}")

        # ==================== HISTÓRICO E COMENTÁRIOS ====================
        st.divider()
        st.subheader("📜 Histórico e Comentários")

        df_hist = fetch_historico_publico_by_qr(qr)

        if not df_hist.empty:
            st.dataframe(df_hist, use_container_width=True, hide_index=True)
        else:
            st.info("Ainda não há comentários ou atualizações registradas.")

        # ==================== DESENHO TÉCNICO ====================
        st.divider()
        st.subheader("🖼️ Desenho Técnico")
        desenho_bytes = load_desenho_tecnico_by_qr(qr)
        if desenho_bytes:
            mime, ext = infer_desenho_mime(desenho_bytes)

            with st.expander("🔍 Visualizar desenho ampliado (zoom)", expanded=False):
                if mime.startswith("image/"):
                    st.image(desenho_bytes, caption="Desenho Técnico", use_container_width=True)
                else:
                    st.info("Pré-visualização disponível apenas para imagens. Use o botão abaixo para baixar o arquivo.")

            st.download_button(
                label="⬇️ Baixar Desenho Técnico",
                data=desenho_bytes,
                file_name=f"desenho_{qr}{ext}",
                mime=mime,
                type="primary",
                use_container_width=True
            )
        else:
            st.info("Nenhum desenho técnico cadastrado para esta peça.")

        # Botão para atualizar status
        if st.button("🔄 Atualizar Status desta peça", type="primary", use_container_width=True):
            st.session_state.scanned_qr = qr
            st.session_state.main_menu = "🔄 Atualizar Status"
            st.query_params.clear()
            st.rerun()

        st.stop()
    else:
        st.error("❌ Peça não encontrada.")
        st.stop()

# ==================== SESSÃO E LOGIN ====================
if "user" not in st.session_state:
    st.session_state.user = None

if not st.session_state.user:
    st.title("🛠️ ALJ Inspeções - Login")
    st.markdown("**Projeto Integrador MEC-4-47**")

    if st.session_state.get("scanned_qr"):
        st.info(f"Faça login para atualizar o status da peça **{st.session_state.scanned_qr}**.")
    
    # ==================== LAYOUT COM VÍDEO PEQUENO ====================
    col_form, col_video = st.columns([3.3, 1])   
    
    with col_form:
        tab_login, tab_register, tab_recover = st.tabs(["🔑 Fazer Login", "📝 Cadastrar Novo Usuário", "🔓 Esqueci minha senha"])
        
        # ====================== LOGIN ======================
        with tab_login:
            with st.form("login_form"):
                nome_ou_email = st.text_input("Nome de usuário ou E-mail")
                senha = st.text_input("Senha", type="password")
                submitted = st.form_submit_button("Entrar", use_container_width=True)
                
                if submitted:
                    if nome_ou_email and senha:
                        df_user = read_sql("""
                            SELECT id, nome, email, funcao, funcao_custom FROM users
                            WHERE (nome = %(login)s OR email = %(login)s)
                            AND senha = %(senha)s
                        """, params={"login": nome_ou_email, "senha": senha}, use_cache=False)
                        if not df_user.empty:
                            st.session_state.user = df_user.iloc[0].to_dict()
                            if st.session_state.get("scanned_qr"):
                                st.session_state.main_menu = "🔄 Atualizar Status"
                            st.rerun()
                        else:
                            st.error("Usuário, e-mail ou senha incorretos!")
                    else:
                        st.error("Preencha todos os campos!")

        # ====================== CADASTRO ======================
        with tab_register:
            if st.session_state.get("cadastro_sucesso", False):
                st.success("✅ Usuário cadastrado com sucesso!", icon="🎉")
                st.session_state.cadastro_sucesso = False

            novo_nome = st.text_input("Nome completo (será seu login)")
            novo_email = st.text_input("E-mail válido")
            nova_senha = st.text_input("Escolha uma senha", type="password")
            funcao = st.selectbox("Função", ["Operador", "Inspetor de Qualidade", "Supervisor", "Gestor"])
            
            if st.button("Cadastrar Usuário", use_container_width=True):
                if novo_nome and novo_email and nova_senha and "@" in novo_email:
                    try:
                        execute("""INSERT INTO users
                                     (nome, email, senha, funcao, funcao_custom)
                                     VALUES (:nome, :email, :senha, :funcao, :funcao_custom)""",
                                  {"nome": novo_nome, "email": novo_email, "senha": nova_senha,
                                   "funcao": funcao, "funcao_custom": None})
                        st.session_state.cadastro_sucesso = True
                        st.rerun()
                    except IntegrityError:
                        st.error("Esse nome ou e-mail já está cadastrado!")
                else:
                    st.error("Preencha todos os campos!")

        # ====================== ESQUECI MINHA SENHA ======================
        with tab_recover:
            st.write("Informe seu **e-mail** ou **nome de usuário**:")
            recover_input = st.text_input("E-mail ou Nome", key="recover_input")
            if st.button("Buscar usuário", use_container_width=True):
                if recover_input:
                    df = read_sql("""
                        SELECT nome, email FROM users
                        WHERE nome = %(input)s OR email = %(input)s
                    """, params={"input": recover_input}, use_cache=False)
                    if not df.empty:
                        st.session_state.recover_user = recover_input
                        st.session_state.recover_user_nome = df.iloc[0]["nome"]
                    else:
                        st.session_state.pop("recover_user", None)
                        st.session_state.pop("recover_user_nome", None)
                        st.error("E-mail ou nome não encontrado!")
                else:
                    st.error("Informe e-mail ou nome de usuário.")

            if st.session_state.get("recover_user"):
                st.success(f"✅ Usuário encontrado: **{st.session_state.recover_user_nome}**")
                nova_senha_recover = st.text_input("Digite sua **nova senha**", type="password", key="recover_new_pw")
                if st.button("Alterar senha", use_container_width=True):
                    if nova_senha_recover:
                        execute(
                            "UPDATE users SET senha = :senha WHERE nome = :input OR email = :input",
                            {"senha": nova_senha_recover, "input": st.session_state.recover_user},
                        )
                        st.session_state.pop("recover_user", None)
                        st.session_state.pop("recover_user_nome", None)
                        st.success("Senha alterada com sucesso!")
                    else:
                        st.error("Digite a nova senha.")

    # ==================== VÍDEO PEQUENO ====================
    with col_video:
             st.video(
            "video_login.mp4",
            format="video/mp4",
            loop=True,
            autoplay=True,
            muted=True
        )
    
    st.stop()
# ==================== MENU + SIDEBAR  ====================
try:
    logo_original = Image.open("ALJ_Inspeções_logo.png").convert("RGB")
  
    logo_resized = logo_original.resize((255, 100), Image.Resampling.LANCZOS)
    st.sidebar.image(logo_resized)
except (FileNotFoundError, OSError):
    st.sidebar.title("ALJ Inspeções")

st.sidebar.success(f"👤 {st.session_state.user['nome']} ({st.session_state.user.get('funcao', '—')})")
if st.sidebar.button("🚪 Sair"):
    st.session_state.user = None
    st.rerun()

menu_options = [
    "📊 Dashboard Geral", "➕ Cadastrar Nova Peça", "🔄 Atualizar Status",
    "📋 Lista de Peças", "🗑️ Gerenciar Peças", "📖 Histórico por Peça",
    "📈 Produtividade", "🖨️ Gerar Etiqueta"
]
menu = st.sidebar.radio("Menu", menu_options, key="main_menu")

if menu != "🔄 Atualizar Status":
    st.session_state.pop("atualizar_status_last_pdf", None)
    st.session_state.pop("scanned_qr", None)

if menu != "🖨️ Gerar Etiqueta":
    st.session_state.pop("gerar_etiqueta_last", None)
    st.session_state.pop("gerar_etiqueta_pdf", None)
    st.session_state.pop("gerar_etiqueta_is_pdf", None)


def _clear_atualizar_download():
    st.session_state.pop("atualizar_status_last_pdf", None)


# ==================== CONFIGURAÇÕES GLOBAIS ====================
APP_URL = "https://alj447.streamlit.app"

def get_app_url() -> str:
    try:
        url = st.secrets.get("APP_URL", APP_URL)
    except Exception:
        url = APP_URL
    return str(url).rstrip("/")

CORES = {
    "Usinagem": "#1E90FF",
    "Inspeção Preliminar": "#FFD700",
    "Tratamento/Intermediário": "#FF8C00",
    "Inspeção Final": "#32CD32",
    "Retrabalho/Não Conforme": "#FF0000"
}

# ==================== ÁREA EXCLUSIVA DO ADMIN ====================
if st.session_state.user.get('nome') == 'admin':
    st.sidebar.divider()
    st.sidebar.subheader("🔴 Administração")

    # 1) Apagar todos os registros com confirmação Sim/Não
    if st.sidebar.button("🗑️ Apagar todos os registros", type="primary"):
        st.session_state.confirm_delete_all = True

    if st.session_state.get("confirm_delete_all"):
        st.sidebar.warning("⚠️ Deseja confirmar a exclusão TOTAL de TODOS os registros?")
        col_sim, col_nao = st.sidebar.columns(2)
        
        with col_sim:
            if st.button("✅ SIM, APAGAR TUDO", type="primary"):
                execute("DELETE FROM pecas")
                execute("DELETE FROM historico")
                st.success("✅ Todos os registros (peças, histórico e produtividade) foram apagados permanentemente!")
                del st.session_state.confirm_delete_all
                st.rerun()
        
        with col_nao:
            if st.button("❌ NÃO, CANCELAR"):
                del st.session_state.confirm_delete_all
                st.rerun()

    # 2) Gerenciar Usuários
    with st.sidebar.expander("👥 Gerenciar Usuários"):
        df_users = load_users()
        st.dataframe(df_users, use_container_width=True, hide_index=True)

        user_to_manage = st.selectbox("Selecione o usuário para editar/excluir", df_users["nome"].tolist())
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✏️ Alterar Função"):
                st.session_state.edit_user = user_to_manage
        with col2:
            if st.button("🗑️ Excluir Usuário", type="primary"):
                st.session_state.delete_user = user_to_manage

        # Alterar função
        if st.session_state.get("edit_user") == user_to_manage:
            current_role = df_users[df_users["nome"] == user_to_manage]["funcao"].iloc[0]
            new_role = st.selectbox("Nova função", ["Operador", "Inspetor de Qualidade", "Outros"], 
                                  index=["Operador", "Inspetor de Qualidade", "Outros"].index(current_role) if current_role in ["Operador", "Inspetor de Qualidade", "Outros"] else 0)
            if st.button("Salvar nova função"):
                execute("UPDATE users SET funcao = :funcao WHERE nome = :nome",
                          {"funcao": new_role, "nome": user_to_manage})
                st.success(f"Função de {user_to_manage} alterada com sucesso!")
                st.rerun()

        # Excluir usuário
        if st.session_state.get("delete_user") == user_to_manage:
            if st.button("✅ SIM, EXCLUIR USUÁRIO", type="primary"):
                execute("DELETE FROM users WHERE nome = :nome", {"nome": user_to_manage})
                st.success(f"Usuário {user_to_manage} excluído!")
                st.rerun()

# ==================== FUNÇÕES QR ====================
def criar_qr_pil(qr_code):
    full_url = f"{get_app_url()}?qr_code={qr_code}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(full_url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")

def gerar_etiqueta(qr_code, tipo_peca, cadastrado_por, responsavel, 
                   data_cadastro, etapa_atual, data_atualizacao, atualizado_por):
    
    cor_etapa = CORES.get(etapa_atual, "#1E90FF")
    
    largura, altura = 1200, 800
    img = Image.new("RGB", (largura, altura), color="white")
    draw = ImageDraw.Draw(img)
        
    draw.rectangle([0, 0, 65, altura], fill=cor_etapa)
        
    # ==================== LOGO  ====================
    try:
        logo_original = Image.open("Logo_QR.png").convert("RGBA")
        logo = logo_original.resize((380, 200), Image.Resampling.LANCZOS)
        logo_com_fundo_branco = Image.new("RGBA", logo.size, (255, 255, 255, 255))
        logo_com_fundo_branco.paste(logo, (0, 0), logo)
        img.paste(logo_com_fundo_branco, (95, 8))
    except (FileNotFoundError, OSError):
        draw.text((100, 60), "ALJ Inspeções", fill="black", font=ImageFont.load_default())

    # ==================== QR CODE ====================
    qr_pil = criar_qr_pil(qr_code)
    qr_img = qr_pil.resize((265, 265))
    img.paste(qr_img, (830, 200))

    # ==================== FONTES ====================
    try:
        font_titulo = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
        font_normal = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
    except (OSError, IOError):
        font_titulo = font_normal = ImageFont.load_default()

    # ==================== FUNÇÃO DE TEXTO COM QUEBRA ====================
    def desenhar_texto(x, y_inicial, texto, font, cor="black", max_largura=720):
        if not texto.strip():
            return y_inicial + 10
        palavras = texto.split()
        linhas = []
        linha_atual = []
        for palavra in palavras:
            linha_teste = ' '.join(linha_atual + [palavra])
            largura_teste = draw.textlength(linha_teste, font=font)
            if largura_teste > max_largura and linha_atual:
                linhas.append(' '.join(linha_atual))
                linha_atual = [palavra]
            else:
                linha_atual.append(palavra)
        if linha_atual:
            linhas.append(' '.join(linha_atual))
        
        y = y_inicial
        for linha in linhas:
            draw.text((x, y), linha, fill=cor, font=font)
            y += font.size + 12
        return y + 8

    # ==================== TEXTOS ====================
    y = 215
    y = desenhar_texto(95, y, f"Nº: {qr_code}", font_titulo)
    y = desenhar_texto(95, y, f"Tipo: {tipo_peca}", font_normal)
    y = desenhar_texto(95, y, f"Cadastrado por: {cadastrado_por}", font_normal)
    y = desenhar_texto(95, y, f"Responsável: {responsavel}", font_normal)
    y = desenhar_texto(95, y, f"Data cadastro: {data_cadastro}", font_normal)
    y = desenhar_texto(95, y, f"Status: {etapa_atual}", font_normal, cor=cor_etapa)
    y = desenhar_texto(95, y, f"Data atualização: {data_atualizacao}", font_normal)
    desenhar_texto(95, y, f"Atualizado por: {atualizado_por}", font_normal)

    return img
                     
# ==================== CADASTRAR NOVA PEÇA ====================
if menu == "➕ Cadastrar Nova Peça":
    if st.session_state.user['funcao'] not in ["Operador", "Gestor", "Supervisor", "Administrador"]:
        st.error("❌ Você não tem permissão para cadastrar peças.")
        st.stop()
    
    st.header("Cadastrar Nova Peça")
    
    # Quem está cadastrando (sempre o usuário logado com cargo)
    cadastrado_por_full = f"{st.session_state.user['funcao']} - {st.session_state.user['nome']}"
    
    # ==================== SELEÇÃO DO RESPONSÁVEL COM CARGO ====================
    if st.session_state.user['funcao'] in ["Gestor", "Supervisor", "Administrador"]:
        df_op = load_operadores()
        op_options = [f"{row['funcao']} - {row['nome']}" for _, row in df_op.iterrows()]
        responsavel_selecionado = st.selectbox("Operador responsável pela peça", op_options or ["Sem operador"], key="resp_cadastro")
    else:
        responsavel_selecionado = f"{st.session_state.user['funcao']} - {st.session_state.user['nome']}"
    
    # ==================== TELA DE SUCESSO (AGORA CORRETA) ====================
    if st.session_state.get("mensagem_sucesso"):
        st.success(st.session_state.mensagem_sucesso)
        st.divider()
        st.subheader("📄 Etiqueta Gerada com Sucesso!")
        
        qr = st.session_state.last_pdf
        df = load_peca_by_qr(qr)
        if not df.empty:
            peca = df.iloc[0]
            
            img = gerar_etiqueta(
                qr_code=qr,
                tipo_peca=peca["tipo_peca"],
                cadastrado_por=peca["cadastrado_por"],      
                responsavel=peca["responsavel"],            
                data_cadastro=peca["data_cadastro"],
                etapa_atual=peca["etapa"],
                data_atualizacao=_peca_data_atualizacao(peca),
                atualizado_por=f"{st.session_state.user['funcao']} - {st.session_state.user['nome']}"
            )

            _render_etiqueta_download(img, qr, "📥 **BAIXAR ETIQUETA**", primary=True)
            
            if st.button("🧹 Limpar e cadastrar nova peça", type="secondary", use_container_width=True):
                for key in ["last_pdf", "mensagem_sucesso", "cad_tipo", "cad_etapa", "cad_obs", "cad_desenho"]:
                    if key in st.session_state:
                        del st.session_state[key]
                st.rerun()
    
    # ==================== FORMULÁRIO ====================
    else:
        with st.form("cadastro_nova_peca", clear_on_submit=True):
            tipo = st.text_input("Tipo da Peça (ex: Eixo, Flange)", key="cad_tipo")
            etapa_inicial = st.selectbox("Etapa Inicial", 
                                       ["Usinagem", "Tratamento/Intermediário"], 
                                       key="cad_etapa")
            obs = st.text_area("Observações iniciais", key="cad_obs")
            desenho = st.file_uploader("Desenho Técnico (PDF ou Imagem)", 
                                     type=["pdf", "png", "jpg", "jpeg"], 
                                     key="cad_desenho")
            submitted = st.form_submit_button("Cadastrar Peça", use_container_width=True)
            
            if submitted:
                if not tipo:
                    st.error("❌ O Tipo da Peça é obrigatório!")
                else:
                    qr_code = f"PECA-{datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%Y%m%d%H%M%S')}"
                    agora = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")
                    if desenho and desenho.size > MAX_DESENHO_BYTES:
                        st.error("❌ O desenho técnico deve ter no máximo 10 MB.")
                    else:
                        desenho_bytes = desenho.read() if desenho else None

                        execute("""INSERT INTO pecas
                                     (qr_code, tipo_peca, cor_atual, status, etapa, responsavel, cadastrado_por,
                                      data_cadastro, data_atualizacao, resultado, data_conclusao,
                                      responsavel_conclusao, desenho_tecnico)
                                     VALUES (:qr_code, :tipo_peca, :cor_atual, :status, :etapa, :responsavel,
                                             :cadastrado_por, :data_cadastro, :data_atualizacao, :resultado,
                                             :data_conclusao, :responsavel_conclusao, :desenho_tecnico)""",
                                {"qr_code": qr_code, "tipo_peca": tipo, "cor_atual": etapa_inicial,
                                 "status": "Em andamento", "etapa": etapa_inicial,
                                 "responsavel": responsavel_selecionado, "cadastrado_por": cadastrado_por_full,
                                 "data_cadastro": agora, "data_atualizacao": agora, "resultado": None,
                                 "data_conclusao": None, "responsavel_conclusao": None,
                                 "desenho_tecnico": desenho_bytes})

                        execute("""INSERT INTO historico
                                     (qr_code, tipo_peca, etapa, cor, status, responsavel, data, observacao)
                                     VALUES (:qr_code, :tipo_peca, :etapa, :cor, :status, :responsavel, :data, :observacao)""",
                                {"qr_code": qr_code, "tipo_peca": tipo, "etapa": etapa_inicial, "cor": etapa_inicial,
                                 "status": "Início", "responsavel": responsavel_selecionado, "data": agora,
                                 "observacao": obs})

                        st.session_state.last_pdf = qr_code
                        st.session_state.mensagem_sucesso = f"✅ Peça cadastrada com sucesso! Código: **{qr_code}**"
                        st.rerun()
          
# ==================== ATUALIZAR STATUS ====================
elif menu == "🔄 Atualizar Status":
    st.header("Atualizar Status da Peça")
    
    df_nao_concluidas = load_pecas_ativas_dropdown()

    PLACEHOLDER = "Selecione uma peça..."

    if df_nao_concluidas.empty:
        st.info("Nenhuma peça em andamento no momento.")
        escolha = PLACEHOLDER
    else:
        opcoes = [PLACEHOLDER] + [
            f"{row['qr_code']} - {row['tipo_peca']}"
            for _, row in df_nao_concluidas.iterrows()
        ]

        scanned = st.session_state.pop("scanned_qr", None)
        if scanned:
            match = next((o for o in opcoes if o.startswith(f"{scanned} - ")), None)
            if match:
                st.session_state.atualizar_status_peca = match
                st.info(f"Peça **{scanned}** selecionada a partir do QR Code.")
            else:
                st.warning(f"A peça **{scanned}** não está em andamento ou não foi encontrada.")

        escolha = st.selectbox(
            "Selecione a peça",
            opcoes,
            index=0,
            key="atualizar_status_peca",
            on_change=_clear_atualizar_download,
        )

    if not df_nao_concluidas.empty and escolha != PLACEHOLDER:
        qr_input = escolha.split(" - ")[0]
        df = load_peca_by_qr(qr_input)
        if not df.empty:
            peca = df.iloc[0]
            if peca.get('resultado') in ["Aprovado", "Reprovado"]:
                st.warning(f"✅ Esta peça já foi **{peca['resultado']}**")
            else:
                st.info(f"Peça atual: **{peca['tipo_peca']}** | Etapa: **{peca['etapa']}**")
                
                nova_etapa = st.selectbox("Nova Etapa", list(CORES.keys()))
                nova_obs = st.text_area("Observações")
                
                if st.button("Atualizar Status"):
                    agora = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")
                    responsavel_full = f"{st.session_state.user['funcao']} - {st.session_state.user['nome']}"
                    if nova_etapa != peca['etapa']:
                        execute("""UPDATE pecas SET etapa=:etapa, cor_atual=:cor_atual, responsavel=:responsavel,
                                            data_atualizacao=:data_atualizacao WHERE qr_code=:qr_code""",
                                  {"etapa": nova_etapa, "cor_atual": nova_etapa, "responsavel": responsavel_full,
                                   "data_atualizacao": agora, "qr_code": qr_input})
                        execute("""INSERT INTO historico
                                     (qr_code, tipo_peca, etapa, cor, status, responsavel, data, observacao)
                                     VALUES (:qr_code, :tipo_peca, :etapa, :cor, :status, :responsavel, :data, :observacao)""",
                                  {"qr_code": qr_input, "tipo_peca": peca['tipo_peca'], "etapa": nova_etapa,
                                   "cor": nova_etapa, "status": "Atualizado", "responsavel": responsavel_full,
                                   "data": agora, "observacao": nova_obs})
                        st.session_state.atualizar_status_last_pdf = qr_input
                        st.toast("✅ Status atualizado!", icon="🎉")
                        st.rerun()
                    elif nova_obs.strip():
                        execute("""INSERT INTO historico
                                     (qr_code, tipo_peca, etapa, cor, status, responsavel, data, observacao)
                                     VALUES (:qr_code, :tipo_peca, :etapa, :cor, :status, :responsavel, :data, :observacao)""",
                                  {"qr_code": qr_input, "tipo_peca": peca['tipo_peca'], "etapa": peca['etapa'],
                                   "cor": peca['cor_atual'], "status": "Atualizado", "responsavel": responsavel_full,
                                   "data": agora, "observacao": nova_obs})
                        st.toast("✅ Observação registrada!", icon="📝")
                        st.rerun()
                    else:
                        st.warning("Selecione uma etapa diferente ou adicione uma observação.")
                
                if peca['etapa'] == "Inspeção Final":
                    st.divider()
                    st.subheader("🎯 Concluir Peça")
                    resultado_final = st.radio("Resultado", ["Aprovado", "Reprovado"], horizontal=True)
                    obs_conclusao = st.text_area("Observações da conclusão")
                    
                    if st.button("✅ CONCLUIR PEÇA", type="primary"):
                        agora = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")
                        responsavel = f"{st.session_state.user['funcao']} - {st.session_state.user['nome']}"
                        execute("""UPDATE pecas
                                     SET resultado=:resultado, responsavel_conclusao=:responsavel_conclusao,
                                         data_conclusao=:data_conclusao
                                     WHERE qr_code=:qr_code""",
                                  {"resultado": resultado_final, "responsavel_conclusao": responsavel,
                                   "data_conclusao": agora, "qr_code": qr_input})
                        execute("""INSERT INTO historico
                                     (qr_code, tipo_peca, etapa, cor, status, responsavel, data, observacao)
                                     VALUES (:qr_code, :tipo_peca, :etapa, :cor, :status, :responsavel, :data, :observacao)""",
                                  {"qr_code": qr_input, "tipo_peca": peca['tipo_peca'], "etapa": peca['etapa'],
                                   "cor": peca['cor_atual'], "status": "Concluída", "responsavel": responsavel,
                                   "data": agora, "observacao": f"Resultado: {resultado_final} | {obs_conclusao}"})
                        st.session_state.atualizar_status_last_pdf = qr_input
                        st.success(f"Peça concluída como **{resultado_final}**!")
                        st.rerun()
        else:
            st.error("QR Code não encontrado!")

    # ==================== DOWNLOAD DA ETIQUETA ====================
    qr_download = st.session_state.get("atualizar_status_last_pdf")
    if (
        qr_download
        and not df_nao_concluidas.empty
        and escolha != PLACEHOLDER
        and qr_download == escolha.split(" - ")[0]
    ):
        df = load_peca_by_qr(qr_download)
        if not df.empty:
            peca = df.iloc[0]

            img = gerar_etiqueta(
                qr_code=qr_download,
                tipo_peca=peca["tipo_peca"],
                cadastrado_por=peca.get("cadastrado_por", peca["responsavel"]),
                responsavel=peca["responsavel"],
                data_cadastro=peca["data_cadastro"],
                etapa_atual=peca["etapa"],
                data_atualizacao=_peca_data_atualizacao(peca),
                atualizado_por=f"{st.session_state.user['funcao']} - {st.session_state.user['nome']}"
            )

            _render_etiqueta_download(img, qr_download, "📄 **BAIXAR ETIQUETA ATUALIZADA**", primary=True)
              
# ==================== GERENCIAR PEÇAS ====================
elif menu == "🗑️ Gerenciar Peças":
    st.header("🗑️ Gerenciar Peças")
    
    df = load_gerenciar_pecas()
    
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        qr_para_acao = st.selectbox("Selecione o QR Code para excluir", df["QR Code"].tolist())
        
        if st.button("🗑️ EXCLUIR esta peça", type="primary"):
            st.session_state.to_delete = qr_para_acao
            st.rerun()
    else:
        st.info("Nenhuma peça em andamento. Todas as peças já foram concluídas.")
      
    if st.session_state.get("to_delete"):
        st.warning("⚠️ Tem certeza? Esta ação não pode ser desfeita.")
        col_sim, col_nao = st.columns(2)
        with col_sim:
            if st.button("✅ SIM, EXCLUIR", type="primary"):
                qr = st.session_state.to_delete
                execute("DELETE FROM pecas WHERE qr_code = :qr", {"qr": qr})
                execute("DELETE FROM historico WHERE qr_code = :qr", {"qr": qr})
                st.success(f"Peça {qr} excluída com sucesso!")
                del st.session_state.to_delete
                st.rerun()
        with col_nao:
            if st.button("❌ Cancelar"):
                del st.session_state.to_delete
                st.rerun()
    
# ==================== DASHBOARD GERAL ====================
elif menu == "📊 Dashboard Geral":
    st.header("📊 Visão Geral da Produção")
    df = load_pecas_ativas_full()
    if not df.empty:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Ativas", len(df))
        col2.metric("Usinagem", len(df[df["etapa"] == "Usinagem"]))
        col3.metric("Inspeção Final", len(df[df["etapa"] == "Inspeção Final"]))
        col4.metric("Retrabalho", len(df[df["etapa"] == "Retrabalho/Não Conforme"]))
        
        color_scale = alt.Scale(domain=list(CORES.keys()), range=list(CORES.values()))
        chart = alt.Chart(df).mark_bar(size=40).encode(
            x=alt.X("etapa:N", sort=list(CORES.keys())),
            y=alt.Y("count():Q"),
            color=alt.Color("etapa:N", scale=color_scale)
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("Nenhuma peça ativa.")

# ==================== LISTA DE PEÇAS ====================
elif menu == "📋 Lista de Peças":
    st.header("Lista Completa de Peças")
        
    df_andamento = load_pecas_ativas_listagem()
    
    if not df_andamento.empty:
        df_andamento = df_andamento.rename(columns={
            "tipo_peca": "Tipo da Peça",
            "etapa": "Etapa",
            "status": "Status",
            "responsavel": "Responsável",
            "data_atualizacao": "Data Atualização",
        })
        df_andamento = df_andamento[
            ["qr_code", "Tipo da Peça", "Etapa", "Status", "Responsável", "Data Atualização"]
        ]
      
    df_concluidas = load_pecas_concluidas_full()
    
    tab_and, tab_conc = st.tabs(["Peças em Andamento", "Peças Concluídas"])
    
    with tab_and:
        if not df_andamento.empty:
            st.dataframe(df_andamento, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhuma peça em andamento no momento.")
    
    with tab_conc:
        if not df_concluidas.empty:
            df_display = df_concluidas[['qr_code', 'tipo_peca', 'resultado', 'responsavel', 
                                       'responsavel_conclusao', 'data_cadastro', 'data_conclusao']].rename(columns={
                'tipo_peca': 'Nome da Peça',
                'resultado': 'Status',
                'responsavel': 'Responsável Cadastro',
                'responsavel_conclusao': 'Quem Concluiu'
            })
            tab_apr, tab_rep = st.tabs(["✅ Aprovadas", "❌ Reprovadas"])
            with tab_apr:
                apr = df_display[df_display['Status'] == "Aprovado"]
                if not apr.empty:
                    st.dataframe(apr, use_container_width=True, hide_index=True)
                else:
                    st.info("Nenhuma aprovada.")
            with tab_rep:
                rep = df_display[df_display['Status'] == "Reprovado"]
                if not rep.empty:
                    st.dataframe(rep, use_container_width=True, hide_index=True)
                else:
                    st.info("Nenhuma reprovada.")
        else:
            st.info("Nenhuma peça concluída ainda.")

# ==================== HISTÓRICO POR PEÇA ====================
elif menu == "📖 Histórico por Peça":
    st.header("Histórico Completo")
        
    df_andamento = load_pecas_ativas_dropdown()
    df_concluidas = load_pecas_concluidas_resumo()
    
    tab_and, tab_conc = st.tabs(["Peças em Andamento", "Peças Concluídas"])
    
    with tab_and:
        if not df_andamento.empty:
            lista_and = df_andamento["qr_code"].tolist()
            qr_sel_and = st.selectbox("Selecione o QR Code (Em Andamento)", lista_and, key="hist_and")
            if qr_sel_and:
                hist = load_historico_by_qr(qr_sel_and)
                st.dataframe(hist, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhuma peça em andamento.")
    
    with tab_conc:
        if not df_concluidas.empty:
            lista_conc = df_concluidas["qr_code"].tolist()
            qr_sel_conc = st.selectbox("Selecione o QR Code (Concluídas)", lista_conc, key="hist_conc")
            if qr_sel_conc:
                hist = load_historico_by_qr(qr_sel_conc)
                st.dataframe(hist, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhuma peça concluída ainda.")
          
# ==================== PRODUTIVIDADE ====================
elif menu == "📈 Produtividade":
    st.header("📈 Produtividade da Equipe")
    
    df_hist = load_produtividade_historico()
    
    if df_hist.empty:
        st.info("Ainda não há dados de produtividade.")
    else:
        meses_unicos = sorted(df_hist['mes'].unique(), reverse=True)
        mes_atual = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m")
        meses_anteriores = [m for m in meses_unicos if m != mes_atual]
        
        opcoes_filtro = ["Mês Atual", "Acumulado do Ano", "─"] + meses_anteriores
        periodo = st.selectbox("Período", opcoes_filtro, index=0)
        
        if periodo == "Mês Atual":
            df_filtrado = df_hist[df_hist['mes'] == mes_atual]
        elif periodo == "Acumulado do Ano":
            df_filtrado = df_hist.copy()
        elif periodo != "─":
            df_filtrado = df_hist[df_hist['mes'] == periodo]
        else:
            df_filtrado = df_hist.copy()

        df_insp = df_hist[
            df_hist["responsavel"].astype(str).str.startswith("Inspetor de Qualidade")
        ][["responsavel", "status", "id", "resultado"]]

        df_pecas_concluidas = load_pecas_concluidas_full()

        tab_op, tab_insp, tab_top3, tab_geral = st.tabs(["🔧 Operadores", "🔍 Inspetores", "🏆 Top 3", "📊 Ranking Geral da Fábrica"])

        # ====================== OPERADORES ======================
        with tab_op:
            st.subheader("Desempenho dos Operadores")
            op = df_filtrado[df_filtrado['status'] == 'Início'].groupby('responsavel').agg(
                Total_Cadastradas=('qr_code', 'nunique')
            ).reset_index()
            
            concluidas = df_filtrado[df_filtrado['status'] == 'Concluída'].groupby('responsavel').agg(
                Concluidas=('qr_code', 'nunique'),
                Aprovadas=('resultado', lambda x: (x == 'Aprovado').sum()),
                Reprovadas=('resultado', lambda x: (x == 'Reprovado').sum()),
                Retrabalho=('etapa_atual', lambda x: (x == 'Retrabalho/Não Conforme').sum()),
            ).reset_index()

            op = op.merge(concluidas, on='responsavel', how='left').fillna(0)
            op = op.astype({
                'Total_Cadastradas': 'int', 'Concluidas': 'int', 'Aprovadas': 'int',
                'Reprovadas': 'int', 'Retrabalho': 'int',
            })
            
            op['Taxa_Conclusao_%'] = _safe_pct_round(op['Concluidas'], op['Total_Cadastradas'])
            op['Taxa_Aprovacao_%'] = _safe_pct_round(op['Aprovadas'], op['Concluidas'])
            
            st.dataframe(op, use_container_width=True)

        # ====================== INSPETORES ======================
        with tab_insp:
            st.subheader("Desempenho dos Inspetores")

            if df_insp.empty:
                st.info("Ainda não há atualizações de Inspetores.")
                insp = pd.DataFrame()
            else:
                insp_updates = df_insp[df_insp['status'] == 'Atualizado']
                insp_conclusions = df_insp[df_insp['status'] == 'Concluída']

                if insp_updates.empty and insp_conclusions.empty:
                    st.info("Ainda não há atualizações de Inspetores.")
                    insp = pd.DataFrame()
                else:
                    if not insp_updates.empty:
                        insp = insp_updates.groupby('responsavel').agg(
                            Total_Inspecionadas=('id', 'count'),
                        ).reset_index()
                    else:
                        insp = pd.DataFrame(columns=['responsavel', 'Total_Inspecionadas'])

                    if not insp_conclusions.empty:
                        concl_agg = insp_conclusions.groupby('responsavel').agg(
                            Aprovadas=('resultado', lambda x: (x == 'Aprovado').sum()),
                            Reprovadas=('resultado', lambda x: (x == 'Reprovado').sum()),
                        ).reset_index()
                        insp = insp.merge(concl_agg, on='responsavel', how='outer')
                    else:
                        insp['Aprovadas'] = 0
                        insp['Reprovadas'] = 0

                    insp = insp.fillna(0).astype({
                        'Total_Inspecionadas': 'int', 'Aprovadas': 'int', 'Reprovadas': 'int',
                    })
                    insp['Total_Conclusoes'] = insp['Aprovadas'] + insp['Reprovadas']
                    insp['Taxa_Aprovacao_%'] = _safe_pct_round(insp['Aprovadas'], insp['Total_Conclusoes'])
                    insp['Taxa_Reprovacao_%'] = _safe_pct_round(insp['Reprovadas'], insp['Total_Conclusoes'])
                    st.dataframe(
                        insp[['responsavel', 'Total_Inspecionadas', 'Aprovadas', 'Reprovadas',
                              'Taxa_Aprovacao_%', 'Taxa_Reprovacao_%']],
                        use_container_width=True,
                    )

        # ====================== TOP 3 ======================
        with tab_top3:
            st.subheader("🏆 Top 3 Operadores")
            top_op = op.nlargest(3, 'Total_Cadastradas')[['responsavel', 'Total_Cadastradas', 'Taxa_Aprovacao_%', 'Taxa_Conclusao_%']] if not op.empty else pd.DataFrame()
            st.dataframe(top_op, use_container_width=True)
            
            st.subheader("🏆 Top 3 Inspetores")
            top_insp = insp.nlargest(3, 'Total_Inspecionadas')[['responsavel', 'Total_Inspecionadas', 'Taxa_Aprovacao_%']] if not insp.empty else pd.DataFrame()
            st.dataframe(top_insp, use_container_width=True)

        # ====================== RANKING GERAL ======================
        with tab_geral:
            st.subheader("📊 Ranking Geral da Fábrica")
            
            df_pecas_periodo = df_pecas_concluidas[
                ["resultado", "etapa", "data_cadastro", "data_conclusao"]
            ]

            if periodo == "Mês Atual":
                mes_filtro = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m")
                df_pecas_periodo = _filtrar_pecas_por_mes(df_pecas_periodo, mes_filtro)
            elif periodo != "Acumulado do Ano" and periodo != "─":
                df_pecas_periodo = _filtrar_pecas_por_mes(df_pecas_periodo, periodo)
            
            pecas_periodo = _pecas_unicas_periodo(df_filtrado)
            cadastradas_periodo = df_filtrado[df_filtrado['status'] == 'Início']['qr_code'].nunique()

            geral = pd.DataFrame({
                'Métrica': [
                    'Total de Peças Cadastradas',
                    'Em Inspeção Preliminar',
                    'Em Retrabalho/Não Conforme',
                    'Em Inspeção Final',
                    '✅ Aprovadas',
                    '❌ Reprovadas'
                ],
                'Quantidade': [
                    cadastradas_periodo,
                    pecas_periodo[pecas_periodo['etapa_atual'] == 'Inspeção Preliminar']['qr_code'].nunique(),
                    pecas_periodo[pecas_periodo['etapa_atual'] == 'Retrabalho/Não Conforme']['qr_code'].nunique(),
                    pecas_periodo[pecas_periodo['etapa_atual'] == 'Inspeção Final']['qr_code'].nunique(),
                    len(df_pecas_periodo[df_pecas_periodo['resultado'] == 'Aprovado']),
                    len(df_pecas_periodo[df_pecas_periodo['resultado'] == 'Reprovado'])
                ]
            })
            st.dataframe(geral, use_container_width=True, hide_index=True)

# ==================== GERAR ETIQUETA ====================
elif menu == "🖨️ Gerar Etiqueta":
    st.header("Gerar Etiqueta Colorida")
    
    df_nao_concluidas = load_pecas_ativas_dropdown()
    
    opcoes = ["🔍 Digitar código manualmente"] + [
        f"{row['qr_code']} - {row['tipo_peca']}"
        for _, row in df_nao_concluidas.iterrows()
    ]
    
    escolha = st.selectbox("Selecione a peça ou digite o código", opcoes)
    
    if escolha == "🔍 Digitar código manualmente":
        qr_input = st.text_input("Digite o QR Code da peça manualmente")
    else:
        qr_input = escolha.split(" - ")[0]
    
    if qr_input:
        df = load_peca_by_qr(qr_input)
        if not df.empty:
            peca = df.iloc[0]

            if st.button("Gerar Etiqueta"):
                img = gerar_etiqueta(
                    qr_code=qr_input,
                    tipo_peca=peca["tipo_peca"],
                    cadastrado_por=peca.get("cadastrado_por", peca["responsavel"]),
                    responsavel=peca["responsavel"],
                    data_cadastro=peca["data_cadastro"],
                    etapa_atual=peca["etapa"],
                    data_atualizacao=_peca_data_atualizacao(peca),
                    atualizado_por=peca.get("responsavel", "—")
                )
                pdf_bytes = _etiqueta_pdf_bytes(img)
                st.session_state.gerar_etiqueta_last = qr_input
                if pdf_bytes:
                    st.session_state.gerar_etiqueta_pdf = pdf_bytes
                    st.session_state.gerar_etiqueta_is_pdf = True
                else:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    st.session_state.gerar_etiqueta_pdf = buf.getvalue()
                    st.session_state.gerar_etiqueta_is_pdf = False
                st.rerun()

            if st.session_state.get("gerar_etiqueta_last") == qr_input and st.session_state.get("gerar_etiqueta_pdf") is not None:
                img = gerar_etiqueta(
                    qr_code=qr_input,
                    tipo_peca=peca["tipo_peca"],
                    cadastrado_por=peca.get("cadastrado_por", peca["responsavel"]),
                    responsavel=peca["responsavel"],
                    data_cadastro=peca["data_cadastro"],
                    etapa_atual=peca["etapa"],
                    data_atualizacao=_peca_data_atualizacao(peca),
                    atualizado_por=peca.get("responsavel", "—")
                )
                st.image(img, caption="Pré-visualização da Etiqueta", use_container_width=True)
                if st.session_state.get("gerar_etiqueta_is_pdf"):
                    st.download_button(
                        label="📄 Baixar Etiqueta",
                        data=st.session_state.gerar_etiqueta_pdf,
                        file_name=f"etiqueta_{qr_input}.pdf",
                        mime="application/pdf",
                    )
                else:
                    st.warning("PDF indisponível neste servidor. Baixando como imagem PNG.")
                    st.download_button(
                        label="📄 Baixar Etiqueta (PNG)",
                        data=st.session_state.gerar_etiqueta_pdf,
                        file_name=f"etiqueta_{qr_input}.png",
                        mime="image/png",
                    )
        else:
            st.error("QR Code não encontrado!")
    else:
        st.info("Selecione ou digite um QR Code para gerar a etiqueta.")
