import streamlit as st
if "run_count" not in st.session_state: st.session_state.run_count = 0
st.session_state.run_count += 1
print(f"\n--- EXECUÇÃO #{st.session_state.run_count} ---")
import pandas as pd
import json, base64, os, re, requests, io, sqlite3, glob
import gspread
from google.oauth2.service_account import Credentials
from google import genai
import yfinance as yf
import datetime
import time
import PIL.Image
import numpy as np
import plotly.express as px
import plotly.io as pio
import warnings

# Suprimir avisos de depreciação do Kaleido para não poluir o log
warnings.simplefilter("ignore", category=DeprecationWarning)

# ===== Configuração da página =====
st.set_page_config(page_title="PlasPrint IA", page_icon="favicon.ico", layout="wide")

def init_db():
    try:
        # Banco de Dados de Produção/Fichas (Sincronizado via BAT)
        conn = sqlite3.connect('fichas_tecnicas.db')
        cursor = conn.cursor()
        
        # Tabela de Fichas Técnicas
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fichas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referencia TEXT NOT NULL,
                produto TEXT NOT NULL,
                decoracao TEXT,
                data_cadastro TEXT,
                tempo_s REAL DEFAULT 0.0,
                cyan REAL DEFAULT 0.0,
                magenta REAL DEFAULT 0.0,
                yellow REAL DEFAULT 0.0,
                black REAL DEFAULT 0.0,
                white REAL DEFAULT 0.0,
                varnish REAL DEFAULT 0.0,
                largura REAL DEFAULT 0.0,
                altura REAL DEFAULT 0.0,
                diametro REAL DEFAULT 0.0,
                print_edge REAL DEFAULT 0.0,
                powergrade REAL DEFAULT 0.0,
                finish_time REAL DEFAULT 0.0,
                intervalo REAL DEFAULT 0.0,
                uv_lamp REAL DEFAULT 0.0,
                obs TEXT,
                image_path TEXT
            )
        ''')

        # Tabela de Produtos
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                tempo_padrao REAL DEFAULT 0.0
            )
        ''')

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fichas_referencia ON fichas(referencia)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fichas_produto ON fichas(produto)")
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"Erro ao inicializar banco de dados: {e}")

init_db()


def get_usd_brl_rate():
    if "usd_brl_cache" in st.session_state:
        cached = st.session_state.usd_brl_cache
        if (datetime.datetime.now() - cached["timestamp"]).seconds < 600:
            return cached["rate"]

    rate = None
    url = "https://economia.awesomeapi.com.br/json/last/USD-BRL"
    max_retries = 3
    for attempt in range(max_retries):
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            data = res.json()
            if "USDBRL" in data and "ask" in data["USDBRL"]:
                rate = float(data["USDBRL"]["ask"])
                break
        except:
            pass

    if rate is None:
        try:
            ticker = yf.Ticker("USDBRL=X")
            hist = ticker.history(period="1d")
            if not hist.empty:
                rate = float(hist["Close"].iloc[-1])
        except:
            pass

    st.session_state.usd_brl_cache = {
        "rate": rate,
        "timestamp": datetime.datetime.now()
    }

    return rate

def parse_money_str(s):
    """Parse string de dinheiro, lidando com formato americano e europeu"""
    s = s.strip()
    if s.startswith('$'):
        s = s[1:]
    
    # Remove espaços
    s = s.replace(" ", "")
    
    # Detectar formato: se tem vírgula antes de ponto, é formato europeu
    # Se tem ponto antes de vírgula (ou só ponto com 3 dígitos antes), é formato americano
    
    # Contar ocorrências
    dot_count = s.count('.')
    comma_count = s.count(',')
    
    if comma_count > 0 and dot_count > 0:
        # Ambos presentes - determinar qual é decimal
        last_comma = s.rfind(',')
        last_dot = s.rfind('.')
        
        if last_comma > last_dot:
            # Formato europeu: 1.234.567,89
            s = s.replace('.', '').replace(',', '.')
        else:
            # Formato americano: 1,234,567.89
            s = s.replace(',', '')
    elif comma_count > 0:
        # Só vírgula - pode ser decimal ou separador de milhares
        if comma_count == 1 and len(s.split(',')[1]) <= 2:
            # Provavelmente decimal europeu: 1234,56
            s = s.replace(',', '.')
        else:
            # Separador de milhares: 1,234,567
            s = s.replace(',', '')
    elif dot_count == 1:
        # Um único ponto - pode ser decimal ou milhar
        parts = s.split('.')
        if len(parts[1]) == 3:
            # Se tem 3 dígitos após o ponto e nenhuma vírgula, 
            # em contexto PT-BR/Industrial costuma ser milhar (ex: 250.000)
            s = s.replace('.', '')
        # Se for != 3, deixamos o ponto para o float() tratar como decimal (ex: 1.50 ou 1.2345)
    elif dot_count > 1:
        # Múltiplos pontos = separador de milhares europeu: 1.234.567
        s = s.replace('.', '')
    # else: formato já está correto
    
    try:
        return float(s)
    except:
        return None

def to_brazilian(n):
    if 0 < n < 0.01:
        n = 0.01
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def format_dollar_values(text, rate):
    # Regex que ignora R$ (Reais) e captura apenas $ (Dólares)
    # Usa negative lookbehind (?<!R) para garantir que não haja um 'R' antes do '$'
    money_regex = re.compile(r'(?<!R)\$\s?([\d.,]+)')
    found = False

    def repl(m):
        nonlocal found
        found = True
        orig = m.group(0)
        val = parse_money_str(orig)
        if val is None or rate is None:
            return orig
        converted = val * float(rate)
        brl = to_brazilian(converted)
        # Escapamos o $ com \ para evitar que o Streamlit interprete como LaTeX (que muda a fonte e esconde o $)
        return f"{orig.replace('$', r'\$')} (R\\$ {brl})"

    formatted = money_regex.sub(repl, text)

    if found:
        if not formatted.endswith("\n"):
            formatted += "\n"
        formatted += "(valores sem impostos)"

    return formatted

def process_response(texto):
    # Detecta apenas $ (Dólares), ignorando R$ (Reais)
    padrao_dolar = r"(?<!R)\$\s?[\d.,]+"
    if re.search(padrao_dolar, texto):
        rate = get_usd_brl_rate()
        if rate:
            return format_dollar_values(texto, rate)
        else:
            return texto
    return texto


def inject_favicon():
    try:
        with open("favicon.ico", "rb") as f:
            data = base64.b64encode(f.read()).decode()
        st.markdown(f'<link rel="icon" href="data:image/x-icon;base64,{data}" type="image/x-icon" />', unsafe_allow_html=True)
    except:
        pass
inject_favicon()

def get_base64_of_jpg(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

def get_base64_font(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

# ===== Carregar imagens, backgrounds e fontes =====
background_image = "background.jpg"
logo_image = "logo.png"

def get_base64_file(path):
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except:
        return ""

img_base64 = get_base64_file(background_image)
img_base64_logo = get_base64_file(logo_image)
font_base64 = get_base64_file("font.ttf")

st.markdown(f"""
<style>
@font-face {{
    font-family: 'SamsungSharpSans';
    src: url(data:font/ttf;base64,{font_base64}) format('truetype');
}}

/* Aplicar fonte ABSOLUTAMENTE em tudo */
* {{
    font-family: 'SamsungSharpSans', sans-serif !important;
}}

[data-testid="stMetricValue"], 
[data-testid="stTable"], 
[data-testid="stDataFrame"],
.stMarkdown, 
div {{
    font-family: 'SamsungSharpSans', sans-serif !important;
}}

/* RESTAURAR ÍCONES E ESCONDER TEXTO VAZADO */
[data-testid="stIconMaterial"], 
.material-icons,
.material-symbols-outlined {{
    font-family: 'Material Symbols Outlined', 'Material Icons' !important;
    display: inline-block !important;
    width: 24px !important;
    height: 24px !important;
    overflow: hidden !important;
    color: inherit !important;
    vertical-align: middle !important;
    font-size: 24px !important;
}}

/* Fix para expanders: esconde o "texto" do ícone mas mantém o label visível */
[data-testid="stExpander"] summary [data-testid="stIconMaterial"] {{
    color: transparent !important; /* Esconde o texto que forma o ícone se o glifo falhar */
    position: relative;
    font-size: 0 !important;
}}

/* Tenta forçar o ícone de volta como SVG ou via renderização do Streamlit */
[data-testid="stExpander"] summary svg {{
    display: block !important;
    color: white !important;
}}

/* Correção para o texto no cabeçalho */
header[data-testid="stHeader"] button {{
    font-size: 0 !important;
    color: transparent !important;
    overflow: hidden !important;
}}

header[data-testid="stHeader"] button * {{
    font-size: 0 !important;
    color: transparent !important;
    display: none !important;
    visibility: hidden !important;
}}

/* Recriar o ícone da sidebar */
[data-testid="stSidebarCollapseButton"]::after {{
    content: "〉" !important;
    visibility: visible !important;
    font-size: 22px !important;
    color: white !important;
    display: block !important;
    position: absolute !important;
    left: 50% !important;
    top: 50% !important;
    transform: translate(-50%, -50%) !important;
    font-family: sans-serif !important;
    pointer-events: none !important;
}}

[data-testid="stSidebarCollapseButton"] {{
    background-color: transparent !important;
    border: none !important;
    width: 40px !important;
    height: 40px !important;
    position: relative !important;
}}

header[data-testid="stHeader"] [data-testid="stHeaderActionElements"] button {{
    font-size: 14px !important;
    color: white !important;
}}

h1.custom-font {{
    text-align: center;
    font-size: 380%;
    margin-bottom: 0px;
}}
p.custom-font {{
    font-weight: bold;
    text-align: left;
}}
.stApp {{
    background-image: url("data:image/jpg;base64,{img_base64}");
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
    background-attachment: fixed;
}}

    /* Efeito Glassmorphism */
    .glass-card {{
        background: rgba(25, 25, 25, 0.6) !important;
        backdrop-filter: blur(15px) !important;
        -webkit-backdrop-filter: blur(15px) !important;
        border-radius: 15px !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        padding: 20px !important;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.8) !important;
        margin-bottom: 25px !important;
    }}

/* Estilização do chat */
.stChatMessage {{
    background-color: rgba(255, 255, 255, 0.05) !important;
    border-radius: 15px !important;
    padding: 10px !important;
    margin-bottom: 10px !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
}}

.stChatInputContainer {{
    padding-bottom: 20px !important;
}}

/* Remover ícones do chat */
[data-testid="stChatMessageAvatarUser"], 
[data-testid="stChatMessageAvatarAssistant"],
.stChatMessageAvatar {{
    display: none !important;
}}

/* Estilizar o botão para largura total e texto 'Enviar Imagem' */
div[data-testid="stFileUploader"] section,
div[data-testid="stFileUploader"] label {{
    width: 100% !important;
    max-width: 100% !important;
    min-width: 100% !important;
    display: block !important;
    padding: 0 !important;
    margin: 0 !important;
}}

div[data-testid="stFileUploader"] label {{
    display: none !important;
}}

div[data-testid="stFileUploader"] section {{
    background-color: transparent !important;
    border: none !important;
    min-height: 0 !important;
    pointer-events: none !important;
}}

div[data-testid="stFileUploader"] svg,
div[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"] {{
    display: none !important;
}}

div[data-testid="stFileUploader"] section button {{
    font-family: 'SamsungSharpSans', sans-serif !important;
    width: 100% !important;
    min-width: 100% !important;
    margin: 10px 0 0 0 !important;
    height: 48px !important;
    background-color: rgba(255, 255, 255, 0.1) !important;
    border: 1px solid rgba(255, 255, 255, 0.2) !important;
    border-radius: 8px !important;
    color: transparent !important;
    position: relative !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    pointer-events: auto !important;
    cursor: pointer !important;
}}

div[data-testid="stFileUploader"] section button::before {{
    display: flex !important;
    content: "Enviar Imagem" !important;
    position: absolute !important;
    width: 100% !important;
    height: 100% !important;
    left: 0 !important;
    top: 0 !important;
    align-items: center !important;
    justify-content: center !important;
    color: white !important;
    font-size: 0.95rem !important;
    font-weight: bold !important;
    pointer-events: none !important;
}}

/* MATAR QUALQUER RASTRO EM OUTROS BOTÕES */
div[data-testid="stFileUploader"] button:not(section button),
div[data-testid="stFileUploaderDeleteBtn"],
div[data-testid="stFileUploaderFileData"] button,
button[aria-label="Remove image"] {{
    width: auto !important;
    min-width: 0 !important;
    background-color: transparent !important;
    border: none !important;
}}

div[data-testid="stFileUploader"] button:not(section button)::before,
div[data-testid="stFileUploader"] button:not(section button)::after,
div[data-testid="stFileUploaderDeleteBtn"]::before,
div[data-testid="stFileUploaderFileData"] button::before {{
    content: none !important;
    display: none !important;
}}


/* Estilo para as abas (Tabs) */
[data-testid="stTab"] p {{
    font-size: 1.1rem !important;
    font-weight: bold !important;
    color: rgba(255, 255, 255, 0.6) !important;
    transition: all 0.3s ease !important;
}}

[data-testid="stTab"][aria-selected="true"] {{
    background-color: rgba(0, 210, 255, 0.08) !important;
    border-bottom: 3px solid #00d2ff !important;
}}

[data-testid="stTab"][aria-selected="true"] p {{
    color: #00d2ff !important;
}}

[data-testid="stTab"]:hover p {{
    color: white !important;
}}

/* Remover a linha vermelha padrão do Streamlit */
[data-testid="stTabList"] div[data-baseweb="tab-highlight"] {{
    background-color: transparent !important;
    display: none !important;
}}
</style>
""", unsafe_allow_html=True)
# Estilos customizados para progress bars e feedback visual
st.markdown("""
<style>
/* Progress bars customizadas - Azul Claro para Azul Royal */
.stProgress > div > div > div > div {
    background: linear-gradient(90deg, #00d2ff 0%, #3a7bd5 100%);
    animation: progressPulse 1.5s ease-in-out infinite;
}

@keyframes progressPulse {
    0%, 100% {
        opacity: 1;
    }
    50% {
        opacity: 0.8;
    }
}

/* Spinner customizado (sem rotação no container) - Azul Claro */
.stSpinner > div {
    border-top-color: #00d2ff !important;
}

/* Alertas mais bonitos com borda azul */
.stAlert {
    border-radius: 10px !important;
    border-left: 4px solid #00d2ff !important;
    animation: slideIn 0.3s ease-out;
}

@keyframes slideIn {
    from {
        opacity: 0;
        transform: translateY(-10px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

/* Success message */
.stSuccess {
    background-color: rgba(40, 167, 69, 0.1) !important;
    border-left-color: #28a745 !important;
}

/* Error message */
.stError {
    background-color: rgba(220, 53, 69, 0.1) !important;
    border-left-color: #dc3545 !important;
}

/* Warning message */
.stWarning {
    background-color: rgba(255, 193, 7, 0.1) !important;
    border-left-color: #ffc107 !important;
}

/* Info message */
.stInfo {
    background-color: rgba(102, 126, 234, 0.1) !important;
    border-left-color: #667eea !important;
}

/* Custom MultiSelect Styling */
div[data-baseweb="select"] > div {
    background-color: rgba(255, 255, 255, 0.05) !important;
    border-radius: 8px !important;
    border: 1px solid rgba(0, 210, 255, 0.2) !important;
}
div[data-testid="stMultiSelect"] span[data-baseweb="tag"] {
    background-color: #1a335f !important;
    color: white !important;
    border-radius: 4px !important;
    border: 1px solid rgba(0, 210, 255, 0.3) !important;
}

/* CSS simplify - removed nuclear fix to test loading */

/* ensure my custom progress bar Pulse still works (it uses opacity) 
   we will scope it specifically so it's not neutralized by the above rule */
.stProgress > div > div > div > div {
    animation: progressPulse 1.5s ease-in-out infinite !important;
}

/* Skeleton visibility restored to avoid blank screen during load */
[data-testid="stSkeleton"] {
    display: block !important;
}

/* Aplicar transições apenas a elementos específicos se necessário */
.stButton, .stTextInput, .stFileUploader, .stChatMessage {
    transition: background-color 0.2s ease, transform 0.2s ease !important;
}

/* --- TRANSFORMAR RADIO EM ABAS (RESTAURANDO O VISUAL) --- */
[data-testid="stRadio"] > div[role="radiogroup"] {
    display: flex;
    flex-direction: row;
    justify-content: center !important;
    align-items: center;
    gap: 10px !important;
    row-gap: 15px !important;
    width: 100% !important;
    flex-wrap: wrap !important;
    background-color: transparent !important;
    padding: 10px 0 !important;
    overflow: visible !important;
}

[data-testid="stRadio"] > div[role="radiogroup"] > label {
    background: transparent !important;
    padding: 8px 15px !important;
    cursor: pointer !important;
    border-radius: 5px !important;
    transition: all 0.3s !important;
    flex: 0 1 auto !important; /* Não força ocupar toda a largura, mantendo o bloco centralizado */
    min-width: fit-content !important;
    text-align: center !important;
    justify-content: center !important;
    margin: 0 !important;
    border: none !important;
    white-space: normal !important; /* Permite quebra de linha interna se o texto for muito longo */
    overflow: visible !important;
}

/* Esconder bolinha do radio (Removendo o que o usuário não pediu) */
[data-testid="stRadio"] > div[role="radiogroup"] > label > div:first-child {
    display: none !important;
}

/* Texto do item - Escala adaptativa para 7 abas */
[data-testid="stRadio"] > div[role="radiogroup"] > label > div[data-testid="stMarkdownContainer"] p {
    font-size: clamp(0.45rem, 0.9vw, 0.85rem) !important;
    font-weight: bold !important;
    color: rgba(255, 255, 255, 0.6) !important;
    margin: 0 !important;
    padding: 0 !important;
}

/* --- RESPONSIVIDADE EXTREMA --- */
@media (max-width: 768px) {
    [data-testid="stRadio"] > div[role="radiogroup"] > label {
        padding: 5px 8px !important;
        flex: 1 1 auto !important; /* Em telas pequenas, pode ser melhor ocupar tudo */
    }
}

/* Hover */
[data-testid="stRadio"] > div[role="radiogroup"] > label:hover {
    background-color: rgba(255, 255, 255, 0.05) !important;
}
[data-testid="stRadio"] > div[role="radiogroup"] > label:hover > div[data-testid="stMarkdownContainer"] p {
    color: white !important;
}

/* Ítem Selecionado (Simulando a aba ativa) */
[data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) {
    border-bottom: 3px solid #00d2ff !important;
    background-color: rgba(0, 210, 255, 0.08) !important;
}

[data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) > div[data-testid="stMarkdownContainer"] p {
    color: #00d2ff !important;
}
</style>
""", unsafe_allow_html=True)


# ===== Segredos =====
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    SHEET_ID = st.secrets["SHEET_ID"]
    SERVICE_ACCOUNT_B64 = st.secrets["SERVICE_ACCOUNT_B64"]
except:
    st.error("Configure os segredos GEMINI_API_KEY, SHEET_ID e SERVICE_ACCOUNT_B64.")
    st.stop()

sa_json = json.loads(base64.b64decode(SERVICE_ACCOUNT_B64).decode())
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(sa_json, scopes=scopes)
gc = gspread.authorize(creds)

try:
    sh = gc.open_by_key(SHEET_ID)
except Exception as e:
    st.error(f"Não consegui abrir a planilha: {e}")
    st.stop()

@st.cache_data
def read_ws(name):
    try:
        ws = sh.worksheet(name)
        return pd.DataFrame(ws.get_all_records())
    except Exception as e:
        st.warning(f"Aba '{name}' não pôde ser carregada: {e}")
        return pd.DataFrame()

@st.cache_data
def read_sqlite(table_name):
    try:
        conn = sqlite3.connect('fichas_tecnicas.db')
        df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
        conn.close()
        return df
    except Exception as e:
        if "no such table" in str(e).lower():
            try:
                # Tenta inicializar e ler novamente uma única vez
                init_db()
                conn = sqlite3.connect('fichas_tecnicas.db')
                df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
                conn.close()
                return df
            except:
                pass
        st.error(f"Erro ao ler banco de dados local ({table_name}): {e}")
        return pd.DataFrame()







def refresh_data():
    """Atualiza todos os dados com feedback visual de progresso"""
    print(">>> Iniciando carregamento de dados...")
    progress_bar = st.sidebar.progress(0)
    status_text = st.sidebar.empty()
    
    status_text.text('Carregando dados de erros...')
    progress_bar.progress(0.15)
    st.session_state.erros_df = read_ws("erros")
    print(f"    - Erros: {len(st.session_state.erros_df)} registros")
    
    
    status_text.text('Carregando dados DACEN...')
    progress_bar.progress(0.45)
    st.session_state.dacen_df = read_ws("dacen")
    print(f"    - DACEN: {len(st.session_state.dacen_df)} registros")
    
    status_text.text('Carregando dados PSI...')
    progress_bar.progress(0.60)
    st.session_state.psi_df = read_ws("psi")
    print(f"    - PSI: {len(st.session_state.psi_df)} registros")
    
    status_text.text('Carregando dados gerais...')
    progress_bar.progress(0.75)
    st.session_state.gerais_df = read_ws("gerais")
    print(f"    - Gerais: {len(st.session_state.gerais_df)} registros")
    



    
    progress_bar.progress(1.0)
    status_text.text('Dados carregados com sucesso!')
    print(">>> Carregamento de dados concluído.")
    time.sleep(0.5)
    progress_bar.empty()
    status_text.empty()

def paginate_dataframe(df, page_size=20, key_prefix="page"):
    """Helper to paginate a dataframe in the UI"""
    if len(df) <= page_size:
        return df
    
    total_pages = (len(df) - 1) // page_size + 1
    page_num = st.number_input(f"Página (de {total_pages})", min_value=1, max_value=total_pages, step=1, key=f"{key_prefix}_num")
    
    start_idx = (page_num - 1) * page_size
    end_idx = start_idx + page_size
    
    st.write(f"Mostrando {start_idx + 1} a {min(end_idx, len(df))} de {len(df)} registros")
    return df.iloc[start_idx:end_idx]

def process_chat_request(prompt, dfs, image=None):
    progress_container = st.empty()
    status_container = st.empty()
    
    try:
        with progress_container:
            progress_bar = st.progress(0)
        
        with status_container:
            st.info('Preparando contexto dos dados...')
        progress_bar.progress(0.20)
        
        with status_container:
            st.info('Processando...')
        progress_bar.progress(0.40)
        context = build_context(dfs)
        
        # Instruções de sistema para o modelo
        system_instruction = f'''
        Você é o Assistente Técnico PlasPrint IA especializado em flexografia e impressão industrial.
        Responda em português brasileiro de forma estritamente técnica e direta.
        **NUNCA use saudações, introduções ou frases de cortesia.**
        Vá direto ao ponto e forneça a solução ou análise técnica imediatamente.
        Baseie-se nos dados das planilhas fornecidas e nos dados de produção (Excel).

        FORMATO DE RESPOSTA:
        - Use **Tabelas Markdown** para apresentar custos, consumos e parâmetros numéricos.
        - Use **Títulos (##)** ou **Negrito** para separar seções (ex: Tempo de Processo, Custos).
        - Use **Listas (bullet points)** para parâmetros técnicos.
        - Mantenha um espaçamento claro entre parágrafos.
        - **PROIBIDO**: Nunca mostre nomes técnicos de colunas do banco de dados (ex: `config_white`, `id`, `referencia`) entre parênteses ou em qualquer lugar da resposta. Use apenas o nome amigável.

        UNIDADES DE MEDIDA - CONSUMO DE TINTA:
        - **IMPORTANTE**: Os valores brutos nas planilhas (ex: 0.057) representam **ml (mililitros) por unidade (garrafa)**.
        - **DIFERENCIAÇÃO VISUAL OBRIGATÓRIA**: Para evitar confusão, nunca mostre o mesmo número para unidade e milheiro.
        - **Consumo Unitário**: Use o valor bruto (ex: 0.057) e a unidade **ml/garrafa**.
        - **Consumo por Milheiro (1.000 un)**: Multiplique o valor bruto por 1.000 e use a unidade **ml/milheiro** (ex: 57 ml).


        TRATAMENTO DE LINKS E MÍDIA:
        - **ESTRUTURA OBRIGATÓRIA**: Para links de imagem, use exatamente: Link de Imagem: [URL].
        - **REGRAS DE RESPOSTA**: Ao citar uma **referência**, você DEVE mostrar também a **decoração** correspondente.
        - **LOCALIZAÇÃO**: Coloque o link imediatamente APÓS descrever o item.
        - **REGRA DE OURO**: Sempre inclua os links das colunas IMAGEM e informações.

        Se a pergunta for sobre OEE ou Eficiência:
        - Analise os dados de Disponibilidade, Performance e Qualidade.
        - Identifique gargalos e motivos de rejeição.

        CONTEXTO DOS DADOS:
        {context}
        '''
        
        full_prompt = [prompt]
        if image:
            full_prompt.append(image)
            with status_container:
                st.info('Analisando imagem enviada...')
        
        # Sistema de retry para lidar com 429 RESOURCE_EXHAUSTED
        max_retries = 5
        retry_delay = 10 # segundos iniciais
        resp = None
        
        for attempt in range(max_retries):
            try:
                # Tenta usar o modelo Flash mais recente disponível
                resp = client.models.generate_content(
                    model="gemini-flash-latest", 
                    contents=full_prompt,
                    config={"system_instruction": system_instruction}
                )
                break # Sucesso, sai do loop
            except Exception as e:
                err_str = str(e).upper()
                if "429" in err_str and attempt < max_retries - 1:
                    # Se atingir o limite, esperamos o tempo de backoff
                    with status_container:
                        st.warning(f"Limite de uso temporário atingido. Aguardando {retry_delay}s para liberar... (Tentativa {attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                    retry_delay *= 2 # Espera progressivamente mais
                else:
                    raise e # Erro fatal ou última tentativa falhou
        
        with status_container:
            st.info('Formatando resposta...')
        progress_bar.progress(0.90)

        # Limpeza mínima: apenas links de imagem redundantes se houver
        clean_text = re.sub(r'Links de imagens:?', '', resp.text, flags=re.IGNORECASE)
        
        # Limpar indicadores de progresso
        progress_bar.progress(1.0)
        time.sleep(0.3)
        progress_container.empty()
        status_container.empty()
        
        # Renderização Inteligente: Texto + Mídia intercalados
        render_smart_response(clean_text)

    except Exception as e:
        if "progress_container" in locals() and progress_container: progress_container.empty()
        if "status_container" in locals() and status_container: status_container.empty()
        st.error(f"Erro ao processar: {e}")
        st.warning('Dica: Tente reformular sua pergunta ou verifique sua conexão.')

if any(k not in st.session_state for k in ["erros_df", "trabalhos_df", "dacen_df", "psi_df", "gerais_df"]):
    with st.spinner('Carregando dados iniciais do sistema...'):
        refresh_data()

st.sidebar.header("Dados carregados")
st.sidebar.write("erros:", len(st.session_state.get("erros_df", [])))
st.sidebar.write("trabalhos:", len(st.session_state.get("trabalhos_df", [])))
st.sidebar.write("dacen:", len(st.session_state.get("dacen_df", [])))
st.sidebar.write("psi:", len(st.session_state.get("psi_df", [])))
st.sidebar.write("gerais:", len(st.session_state.get("gerais_df", [])))


if st.sidebar.button("Atualizar Dados"):
    with st.spinner('Atualizando dados...'):
        refresh_data()
    st.success('Dados atualizados!')
    time.sleep(0.5)
    st.rerun()


os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
client = genai.Client()

def build_context(dfs, max_chars=30000):
    parts = []
    for name, df in dfs.items():
        if df.empty:
            continue
        parts.append(f"--- {name} ---")
        for r in df.to_dict(orient="records"):
            row_items = [f"{k}: {v}" for k,v in r.items() if v is not None and str(v).strip() != '']
            parts.append(" | ".join(row_items))
    context = "\n".join(parts)
    if len(context) > max_chars:
        context = context[:max_chars] + "\n...[CONTEXTO TRUNCADO]"
    return context

@st.cache_data
def load_drive_media(url):
    """Baixa os bytes da mídia do Drive para garantir exibição correta"""
    try:
        file_id = ""
        if "/file/d/" in url: file_id = url.split("/file/d/")[1].split("/")[0]
        elif "id=" in url: file_id = url.split("id=")[1].split("&")[0]
        
        if not file_id: return None
        
        direct_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        res = requests.get(direct_url, timeout=10)
        if res.status_code == 200:
            return res.content
    except:
        pass
    return None

def get_media_type(url):
    """Identifica mídia por extensão ou padrão de URL, com suporte especial ao Drive"""
    url_lower = url.lower()
    
    # Check by extension first
    if any(ext in url_lower for ext in ['.mp4', '.mov', '.avi', '.m4v', '.webm', '.mkv']):
        return 'video'
    if any(ext in url_lower for ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp']):
        return 'image'
        
    # Special keywords in URL or common Drive sharing patterns
    if "drive.google.com" in url:
        # If it doesn't have an extension, we'll rely on the AI tag or a later request
        return 'drive'
        
    return 'unknown'

def render_smart_response(text):
    """Renderiza texto e mídia de forma intercalada, detectando links de forma robusta"""
    # Procura por "Link de X: URL" ou apenas URLs de mídia soltas
    pattern = r'((?:Link de [A-Za-zãõí\s]+:?\s*)?https?://[^\s\)\n]+)'
    
    parts = re.split(pattern, text, flags=re.IGNORECASE)
    
    for part in parts:
        if not part: continue
        
        match = re.match(r'(?:Link de ([A-Za-zãõí\s]+):?\s*)?(https?://[^\s\)\n]+)', part, re.IGNORECASE)
        
        if match:
            tag = (match.group(1) or "").lower().strip()
            url = match.group(2).strip().replace('`', '')
            
            # Limpa URL de possíveis resíduos de Markdown ou pontuação final
            url = re.sub(r'[.\)\]\s]+$', '', url)
            
            mtype = get_media_type(url)
            
            try:
                # Decidir se é vídeo ou imagem baseado na tag da IA ou tipo detectado
                is_video = 'vídeo' in tag or 'video' in tag or mtype == 'video'
                is_image = 'imagem' in tag or 'foto' in tag or mtype == 'image'
                
                # Para links do Drive, tentamos inferir se é mídia se a tag for genérica
                if mtype == 'drive' and not is_video and not is_image:
                    if any(x in tag for x in ['máquina', 'foto', 'equipamento', 'mídia', 'apresentação']):
                        is_image = True 

                if is_video:
                    if "drive.google.com" in url:
                        file_id = ""
                        if "/file/d/" in url: file_id = url.split("/file/d/")[1].split("/")[0]
                        elif "id=" in url: file_id = url.split("id=")[1].split("&")[0]
                        st.video(f"https://drive.google.com/uc?id={file_id}")
                    else:
                        st.video(url)
                    st.markdown(f"<div style='text-align:center;'><a href='{url}' target='_blank' style='color: #00d2ff;'>Abrir vídeo em nova aba</a></div>", unsafe_allow_html=True)
                elif is_image or mtype == 'image':
                    if "drive.google.com" in url:
                        img_bytes = load_drive_media(url)
                        if img_bytes:
                            st.image(img_bytes, use_container_width=True)
                        else:
                            st.markdown(f"<div style='text-align:center;'><a href='{url}' target='_blank' style='color: #00d2ff;'>Ver Foto (Clique aqui)</a></div>", unsafe_allow_html=True)
                    else:
                        st.image(url, use_container_width=True)
                else:
                    # Se não for mídia clara, mostra botão azul
                    st.markdown(f"<div style='text-align:center; margin: 10px 0;'><a href='{url}' target='_blank' style='background-color: #3a7bd5; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;'>Abrir Conteúdo ({tag or 'Link'})</a></div>", unsafe_allow_html=True)
            except Exception:
                st.markdown(f"🔗 [Acesse o conteúdo aqui]({url})")
        else:
            clean_part = part.strip()
            if clean_part:
                clean_part = re.sub(r'^[\s\n]*[\*\-]\s*', '', clean_part)
                if clean_part:
                    st.markdown(process_response(clean_part))


def remove_drive_links(text):
    return re.sub(r'https?://drive\.google\.com/file/d/[a-zA-Z0-9_-]+/view\?usp=drive_link', '', text)

col_esq, col_meio, col_dir = st.columns([1,3,1])
with col_meio:
    st.markdown("<h1 class='custom-font'>PlasPrint IA</h1><br>", unsafe_allow_html=True)

with col_dir:
    pass  # Coluna direita vazia


with col_meio:





    if True: # Removida navegação, mantendo apenas Assistente
        # Input do chat
        prompt = st.chat_input("Qual a sua dúvida?")

        # Upload de imagem
        uploaded_file = st.file_uploader("Enviar Imagem", type=["jpg", "jpeg", "png"], label_visibility="collapsed")

        if prompt:
            # Mostrar mensagem do usuário
            with st.chat_message("user"):
                st.markdown(prompt)
                image_to_send = None
                if uploaded_file:
                    image_to_send = PIL.Image.open(uploaded_file)
                    st.image(image_to_send, caption="Imagem enviada", use_container_width=True)

            # Processar resposta
            with st.chat_message("assistant"):
                dfs = {
                    "erros": st.session_state.erros_df,
                    "trabalhos": pd.DataFrame(),
                    "dacen": st.session_state.dacen_df,
                    "psi": st.session_state.psi_df,
                    "gerais": st.session_state.gerais_df
                }
                

                process_chat_request(prompt, dfs, image_to_send)





# Footer
footer_css = """
<style>
.footer-container { width: 100%; text-align: center; margin-top: 50px; padding-bottom: 20px; }
.logo-footer { width: 120px; opacity: 0.6; transition: opacity 0.3s ease; margin-bottom: 10px; }
.logo-footer:hover { opacity: 1.0; }
.version-tag { font-size: 12px; color: white; opacity: 0.5; }
[data-testid="stAppViewBlockContainer"] { padding-bottom: 150px !important; }
</style>
"""

footer_html = f"""
<div class='footer-container'>
    <img src="data:image/png;base64,{img_base64_logo}" class="logo-footer"><br>
    <div class='version-tag'>V2.0</div>
</div>
"""

st.markdown(footer_css + footer_html, unsafe_allow_html=True)
print(">>> Script finalizado e pronto para exibir na tela.")










