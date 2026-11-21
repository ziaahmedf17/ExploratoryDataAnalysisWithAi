"""EDA Terminal — ask questions about a CSV/Excel file in plain English.

PandasAI v2 (SmartDataframe) is used deliberately: it generates plain pandas code
on the dataframe (dfs[0]...). PandasAI v3 always routes data access through SQL.

UI concept: a "split terminal". Each question renders as a block with the generated
pandas code on the left and its output on the right.
"""

import base64
import datetime
import hashlib
import hmac
import html
import io
import os
import re
import threading
import time
import traceback
import uuid
from importlib.metadata import version as pkg_version
from pathlib import Path

# ---------------------------------------------------------------- runtime folders
# PandasAI v2 writes charts to <workspace>/exports/charts and cache files to
# <workspace>/cache. Pin the workspace to this app's folder and create the
# folders up front (Streamlit Cloud starts from a clean checkout).
APP_DIR = Path(__file__).resolve().parent
os.environ.setdefault("PANDASAI_WORKSPACE", str(APP_DIR))
os.environ.setdefault("MPLBACKEND", "Agg")  # headless matplotlib (servers, threads)
for _folder in ("exports/charts", "cache"):
    (APP_DIR / _folder).mkdir(parents=True, exist_ok=True)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402
from cycler import cycler  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from fpdf import FPDF  # noqa: E402
from pandasai import SmartDataframe  # noqa: E402

from pandasai_v2_adapters import LiteLLM, TypedResponseParser  # noqa: E402

# ---------------------------------------------------------------- palette
BG, BG2, BG3 = "#121211", "#1B1B19", "#161615"
TEXT, MUTED = "#D3D1C7", "#8F8D84"
GREEN, RED, BORDER = "#97C459", "#E8806B", "#2E2E2B"
CHART_COLORS = [GREEN, "#8F8D84", "#C5E39A", "#5E8A33", "#B9B7AD", "#4D4D48"]

# Dark chart style for every matplotlib figure PandasAI's generated code draws.
plt.rcParams.update({
    "figure.facecolor": "none", "axes.facecolor": "none",
    "savefig.facecolor": "none", "savefig.transparent": True, "savefig.dpi": 160,
    "figure.dpi": 110, "figure.figsize": (7, 4.4),
    "text.color": TEXT, "axes.labelcolor": TEXT, "axes.titlecolor": TEXT,
    "xtick.color": TEXT, "ytick.color": TEXT, "axes.edgecolor": BORDER,
    "axes.grid": True, "axes.axisbelow": True, "grid.color": BORDER, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.prop_cycle": cycler(color=CHART_COLORS),
    "font.family": "monospace", "font.monospace": ["DejaVu Sans Mono"], "font.size": 12,
    "xtick.labelsize": 11, "ytick.labelsize": 11, "axes.labelsize": 12,
    "axes.titlesize": 14, "axes.titleweight": "bold", "axes.titlepad": 14,
    "legend.frameon": False, "patch.edgecolor": "none", "lines.linewidth": 2,
})


# ---------------------------------------------------------------- settings / secrets
def _load_secrets() -> dict:
    """st.secrets as a plain dict; empty when no secrets.toml exists (local runs)."""
    try:
        return {k: v for k, v in st.secrets.items()}
    except Exception:
        return {}


# Local runs: .env -> os.environ. Deployed runs: st.secrets wins over everything.
load_dotenv(APP_DIR / ".env", override=True)
SECRETS = _load_secrets()

# LiteLLM reads provider keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, ...) from the
# environment, so copy top-level secret values there (except the app password).
for _name, _value in SECRETS.items():
    if _name != "APP_PASSWORD" and isinstance(_value, (str, int, float)):
        os.environ[_name] = str(_value)


def get_setting(name: str, default=None):
    """st.secrets first, then environment / .env, then the default."""
    value = SECRETS.get(name)
    if value not in (None, ""):
        return str(value)
    return os.getenv(name) or default


LLM_MODEL = get_setting("LLM_MODEL", "gpt-4.1-mini")
APP_PASSWORD = get_setting("APP_PASSWORD")
API_KEY_FROM_SECRETS = any(k.endswith("_API_KEY") for k in SECRETS)
PANDASAI_VERSION = pkg_version("pandasai")
SAMPLE_FILE = APP_DIR / "sample_data" / "titanic.csv"

# ---- Branding ----
OWNER_NAME = "Zia Ahmed"
PORTFOLIO_URL = "https://ziaahmed.dev"
GITHUB_URL = "https://github.com/ziaahmedf17"
LINKEDIN_URL = "https://www.linkedin.com/in/ziaahmedf17/"

st.set_page_config(page_title="EDA Terminal", page_icon=":material/terminal:", layout="wide")

# ---------------------------------------------------------------- styles
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap');
:root {
  --bg:#121211; --bg2:#1B1B19; --bg3:#161615; --text:#D3D1C7; --muted:#8F8D84;
  --green:#97C459; --red:#E8806B; --border:#2E2E2B;
  --mono:'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  --sans:'Inter', system-ui, sans-serif;
}
/* ---------- chrome: no header/menu/footer/deploy, no shadows ---------- */
[data-testid="stHeader"] {background: transparent; height: 2.5rem;}
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
#MainMenu, footer, .stAppDeployButton, [data-testid="stAppDeployButton"] {display: none !important;}
[data-testid="stMainBlockContainer"] {padding-top: 1.4rem; padding-bottom: 0; max-width: 1440px;}
button, [data-testid="stChatInput"], [data-baseweb="input"], [data-testid="stFileUploaderDropzone"],
[data-testid="stVerticalBlockBorderWrapper"], [data-testid="stExpander"] {box-shadow: none !important;}
:focus-visible {outline: 1px solid var(--green) !important; outline-offset: 1px;}

.mono {font-family: var(--mono);}
.g {color: var(--green);}
.m {color: var(--muted);}

/* ---------- top bar ---------- */
.topbar {display: flex; justify-content: space-between; align-items: center; gap: 12px;
         flex-wrap: wrap; padding-bottom: 14px; border-bottom: 1px solid var(--border);}
.wordmark {font-family: var(--mono); font-size: 18px; font-weight: 700; color: var(--text);
           letter-spacing: -.01em; white-space: nowrap;}
.wordmark .proto {color: var(--green);}
.tags {display: flex; gap: 6px; flex-wrap: wrap;}
.tag {font-family: var(--mono); font-size: 11.5px; color: var(--muted); border: 1px solid var(--border);
      border-radius: 6px; padding: 3px 8px; white-space: nowrap;}
.tag b {color: var(--text); font-weight: 500;}

/* ---------- command bar ---------- */
.st-key-cmdbar {margin-top: 4px;}
.st-key-cmdbar [data-testid="stChatInput"] {background: var(--bg2); border: 1px solid var(--border);
      border-radius: 8px; position: relative; min-height: 56px; display: flex; align-items: center;}
.st-key-cmdbar [data-testid="stChatInput"]:focus-within {border-color: var(--green);}
.st-key-cmdbar [data-testid="stChatInput"] > div {border: none !important; background: transparent !important;}
.st-key-cmdbar [data-testid="stChatInput"]::before {content: ">"; position: absolute; left: 18px; top: 50%;
      transform: translateY(-50%); font-family: var(--mono); font-size: 16px; font-weight: 700;
      color: var(--green); pointer-events: none; z-index: 2;}
.st-key-cmdbar [data-testid="stChatInput"]::after {content: "\\2318 K"; position: absolute; right: 58px; top: 50%;
      transform: translateY(-50%); font-family: var(--mono); font-size: 11px; color: var(--muted);
      border: 1px solid var(--border); border-radius: 5px; padding: 2px 7px; pointer-events: none;}
.st-key-cmdbar textarea {font-family: var(--mono) !important; font-size: 15px !important;
      color: var(--text) !important; padding-left: 40px !important; padding-right: 64px !important;}
.st-key-cmdbar textarea::placeholder {color: var(--muted) !important; opacity: 1;}
.st-key-cmdbar [data-testid="stChatInputSubmitButton"] {color: var(--green);}

/* suggestion chips */
.st-key-chips {gap: 8px !important; margin-top: -4px; align-items: center;}
.st-key-chips [data-testid="stElementContainer"]:has(.chips-label) {flex: 0 0 auto !important; width: auto !important;}
.chips-label {font-family: var(--mono); font-size: 11px; color: var(--muted); letter-spacing: .06em;
              text-transform: uppercase; align-self: center;}
.st-key-chips .stButton > button {font-family: var(--mono); font-size: 12px; min-height: 30px; padding: 2px 12px;
      background: transparent; color: var(--muted); border: 1px solid var(--border); border-radius: 8px;}
.st-key-chips .stButton > button:hover {color: var(--green); border-color: var(--green);}

/* ---------- buttons ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
      font-family: var(--mono); font-size: 12.5px; border-radius: 8px; border: 1px solid var(--border);
      background: transparent; color: var(--text);}
.stButton > button:hover, .stDownloadButton > button:hover {border-color: var(--green); color: var(--green);}
.stButton button p, .stDownloadButton button p, .stFormSubmitButton button p {font-family: var(--mono) !important;
      font-size: inherit !important; font-weight: inherit;}
.stButton > button[kind="primary"], .stFormSubmitButton > button {
      background: var(--green); border-color: var(--green); color: #10110E; font-weight: 700;}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover {
      background: #A9D46E; border-color: #A9D46E; color: #10110E;}
.stDownloadButton > button {min-height: 30px; padding: 2px 12px; font-size: 12px; color: var(--muted);}

/* ---------- answer blocks ---------- */
[class*="st-key-blk_"], .st-key-running {background: var(--bg3); border-radius: 8px;}
[class*="st-key-blk_"] {margin-bottom: 4px;}
.blk-head {display: flex; justify-content: space-between; align-items: baseline; gap: 16px;
           padding-bottom: 10px; border-bottom: 1px solid var(--border); flex-wrap: wrap;}
.blk-q {font-family: var(--mono); font-size: 14px; color: var(--text); font-weight: 500;
        overflow-wrap: anywhere;}
.blk-q .prompt {color: var(--green); font-weight: 700; margin-right: 4px;}
.blk-meta {font-family: var(--mono); font-size: 11.5px; color: var(--muted); white-space: nowrap;}
.blk-meta b {color: var(--text); font-weight: 500;}
.pane-head {display: flex; justify-content: space-between; align-items: center; margin: 2px 0 8px;}
.pane-title {font-family: var(--mono); font-size: 11px; color: var(--muted); letter-spacing: .08em;
             text-transform: uppercase;}
.badge {font-family: var(--mono); font-size: 10.5px; letter-spacing: .08em; padding: 2px 8px;
        border-radius: 5px; border: 1px solid var(--border); color: var(--green);}
.badge.error {color: var(--red); border-color: #4A2C25;}
.badge.stopped {color: var(--muted);}
/* code pane: st.code + line numbers */
[class*="st-key-code"] [data-testid="stCode"] pre {background: var(--bg) !important; border: 1px solid var(--border);
      border-radius: 8px; font-size: 12.5px;}
[class*="st-key-code"] [data-testid="stCode"] code {overflow-wrap: anywhere; word-break: break-word;}
/* copy button: Streamlit renders it on hover, top right of the code pane */
[data-testid="stCodeCopyButton"] {color: var(--muted) !important;}
[data-testid="stCodeCopyButton"]:hover {color: var(--green) !important;}
.nocode {font-family: var(--mono); font-size: 12.5px; color: var(--muted); padding: 12px 14px;
         border: 1px dashed var(--border); border-radius: 8px;}
/* output pane */
.num {font-family: var(--mono); font-size: 48px; font-weight: 500; color: var(--green); line-height: 1.1;
      letter-spacing: -.02em; overflow-wrap: anywhere; padding-top: 6px;}
.num-label {font-family: var(--mono); font-size: 11.5px; color: var(--muted); margin-top: 8px;}
.str {font-family: var(--mono); font-size: 13.5px; line-height: 1.6; white-space: pre-wrap; color: var(--text);
      background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px;}
.err {font-family: var(--mono); font-size: 13px; color: var(--red); background: #1E1512;
      border: 1px solid #4A2C25; border-radius: 8px; padding: 12px 14px; overflow-wrap: anywhere;}
.err b {font-weight: 700;}
.sigint {font-family: var(--mono); font-size: 13px; color: var(--muted); border: 1px dashed var(--border);
         border-radius: 8px; padding: 12px 14px;}
.running-line {font-family: var(--mono); font-size: 13px; color: var(--muted);}
.dl-label {font-family: var(--mono); font-size: 10.5px; color: var(--muted); letter-spacing: .08em;
           text-transform: uppercase; margin: 6px 0 -6px;}
[data-testid="stImage"] img {border-radius: 6px;}

/* ---------- status bar (pinned to the viewport bottom, like an editor footer) ---------- */
[data-testid="stMainBlockContainer"] {min-height: 100vh; display: flex; flex-direction: column;}
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {flex: 1 0 auto;}
[data-testid="stLayoutWrapper"]:has(> .st-key-statusbar) {margin-top: auto; position: sticky; bottom: 0;
      z-index: 50; background: var(--bg); border-top: 1px solid var(--border); padding: 10px 0 16px;}
.st-key-statusbar {min-height: 22px;}
.statusbar {font-family: var(--mono); font-size: 12px; color: var(--muted); white-space: nowrap;
            overflow-x: auto; scrollbar-width: none;}
.statusbar b {color: var(--text); font-weight: 500;}
.statusbar .mode {color: #10110E; background: var(--green); font-weight: 700; padding: 1px 7px;
                  border-radius: 4px; margin-right: 10px; font-size: 11px;}

/* ---------- sidebar "session" panel ---------- */
[data-testid="stSidebar"] {border-right: 1px solid var(--border);}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {padding-top: 1rem;}
.side-title {font-family: var(--mono); font-size: 11px; color: var(--muted); letter-spacing: .1em;
             text-transform: uppercase; margin-bottom: 2px;}
[data-testid="stSidebar"] [data-testid="stFileUploader"] label {display: none;}
[data-testid="stFileUploaderDropzone"] {background: var(--bg2); border: 1px dashed var(--border) !important;}
[data-testid="stFileUploaderDropzoneInstructions"] span, [data-testid="stFileUploaderDropzoneInstructions"] small {
      font-family: var(--mono);}
[data-testid="stFileUploaderDropzoneInstructions"] span {font-size: 12.5px;}
[data-testid="stFileUploaderDropzone"] button p {font-family: var(--mono) !important; font-size: 12px;}
.tree {font-family: var(--mono); font-size: 12px; line-height: 1.75; max-height: 46vh; overflow-y: auto;
       border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; background: var(--bg3);}
.tree-root {color: var(--text); font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
.tree-row {display: flex; gap: 6px; white-space: nowrap;}
.tree-row .br {color: #55544E;}
.tree-row .col {color: var(--text); overflow: hidden; text-overflow: ellipsis; flex: 1;}
.tree-row .dt {color: var(--muted);}
.tree-row .dt.dtn {color: var(--green);}
.side-foot {font-family: var(--mono); font-size: 11px; color: var(--muted); margin-top: 8px; line-height: 1.6;}
.side-foot a {color: var(--muted) !important; text-decoration: none; border-bottom: 1px solid var(--border);}
.side-foot a:hover {color: var(--green) !important; border-color: var(--green);}

/* ---------- empty state: fake terminal ---------- */
.term {max-width: 640px; margin: 7vh auto 18px; border: 1px solid var(--border); border-radius: 8px;
       background: var(--bg3); overflow: hidden;}
.term-bar {display: flex; align-items: center; gap: 6px; padding: 9px 12px; border-bottom: 1px solid var(--border);
           background: var(--bg2);}
.term-bar .dot {width: 9px; height: 9px; border-radius: 50%; background: #34342F;}
.term-bar .title {font-family: var(--mono); font-size: 11.5px; color: var(--muted); margin-left: 8px;}
.term-body {padding: 18px 20px 22px; font-family: var(--mono); font-size: 14px; line-height: 1.9; color: var(--text);}
.tl {display: flex; align-items: center;}
.tl .t {display: inline-block; overflow: hidden; white-space: nowrap; width: 0;
        animation: type var(--d) steps(var(--n)) forwards; animation-delay: var(--delay);}
@keyframes type {to {width: calc(var(--n) * 1ch);}}
.steps {max-width: 640px; margin: 18px auto 0; font-family: var(--mono); font-size: 12.5px; color: var(--muted);
        line-height: 2;}
.steps span.n {color: var(--green); margin-right: 10px;}
.steps b {color: var(--text); font-weight: 500;}

/* ---------- password gate ---------- */
.gate {margin: 14vh 0 18px;}
.gate .line {font-family: var(--mono); font-size: 13.5px; color: var(--text); margin-top: 18px;}
.gate .sub {font-family: var(--sans); font-size: 13px; color: var(--muted); margin-top: 4px;}
[data-testid="stForm"] {border: 1px solid var(--border); background: var(--bg3);}
[data-testid="stTextInput"] input {font-family: var(--mono);}
[data-testid="stTextInput"] label p {font-family: var(--mono); font-size: 11.5px; color: var(--muted);
      text-transform: uppercase; letter-spacing: .08em;}

/* ---------- responsive ---------- */
@media (max-width: 640px) {
  .num {font-size: 38px;}
  .st-key-cmdbar [data-testid="stChatInput"]::after {display: none;}
  .st-key-cmdbar textarea {padding-right: 12px !important;}
  .blk-meta {white-space: normal;}
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def esc(text) -> str:
    return html.escape(str(text))


# ---------------------------------------------------------------- password gate
def password_gate() -> None:
    """Stop the app until the visitor enters APP_PASSWORD.

    - APP_PASSWORD set (st.secrets or .env): password required.
    - Not set, but an API key comes from st.secrets (i.e. deployed): refuse to run,
      so a forgotten password never exposes the API key to strangers.
    - Not set and running locally from .env: no gate (local development).
    """
    if not APP_PASSWORD:
        if API_KEY_FROM_SECRETS:
            st.markdown('<div class="err"><b>ERR:</b> app locked. Set APP_PASSWORD in the app secrets.</div>',
                        unsafe_allow_html=True)
            st.stop()
        return
    if st.session_state.get("authenticated"):
        return

    _, col, _ = st.columns([1, 1.1, 1])
    with col:
        st.markdown(
            '<div class="gate"><div class="wordmark"><span class="proto">EDA</span> Terminal'
            '</div>'
            '<div class="line"><span class="g">$</span> auth --required</div>'
            '<div class="sub">This session is private. Enter the access key to continue.</div></div>',
            unsafe_allow_html=True,
        )
        with st.form("login", border=True):
            password = st.text_input("access key", type="password", placeholder="••••••••")
            submitted = st.form_submit_button("unlock", width="stretch")
        if submitted:
            if hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
                st.session_state.authenticated = True
                st.rerun()
            time.sleep(1)  # slow down password guessing
            st.markdown('<div class="err"><b>ERR:</b> access denied</div>', unsafe_allow_html=True)
    st.stop()


password_gate()


# ---------------------------------------------------------------- LLM setup
@st.cache_resource(show_spinner=False)
def get_llm(model: str) -> LiteLLM:
    """One LiteLLM-backed PandasAI v2 LLM per model name."""
    return LiteLLM(model=model)


def make_smart_df(df: pd.DataFrame) -> SmartDataframe:
    return SmartDataframe(
        df,
        config={
            "llm": get_llm(LLM_MODEL),
            "response_parser": TypedResponseParser,  # keep result type for rendering
            "enable_cache": False,      # always generate fresh code per question
            "open_charts": False,       # never pop up a window on the server
            "save_charts": False,
            "verbose": False,
        },
    )


# ---------------------------------------------------------------- data loading
EXCEL_EXTS = (".xlsx", ".xls")


def read_csv_with_fallback(raw: bytes) -> pd.DataFrame:
    """Read CSV bytes, trying utf-8 first and falling back to latin-1."""
    last_err = None
    for encoding in ("utf-8", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=encoding)
        except UnicodeDecodeError as err:
            last_err = err
    raise last_err  # latin-1 decodes any byte, so this is practically unreachable


@st.cache_data(show_spinner=False)
def excel_sheet_names(raw: bytes) -> list:
    return pd.ExcelFile(io.BytesIO(raw)).sheet_names


def read_table(raw: bytes, name: str, sheet=None) -> pd.DataFrame:
    if name.lower().endswith(EXCEL_EXTS):
        return pd.read_excel(io.BytesIO(raw), sheet_name=sheet or 0)
    return read_csv_with_fallback(raw)


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024


def is_numeric(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


# ---------------------------------------------------------------- query helpers
V2_FAILURE_PREFIX = "Unfortunately, I was not able to"
EXPORT_WORDS = re.compile(r"\b(pdf|csv|excel|xlsx|xls|download|export|save|file)\b", re.I)
CHART_WORDS = re.compile(r"\b(plot|chart|graph|visuali[sz]e|histogram|bar|line|pie|scatter|heatmap|distribution)\b", re.I)
EXPORT_HINT = (
    "\n\n(Note: return the requested data as a dataframe result. "
    "Do not write any files or import extra libraries; the app handles file downloads.)"
)
CHART_HINT = "\n\n(Keep matplotlib's default colors: do not set color or style arguments.)"


def build_query(question: str) -> str:
    query = question
    if EXPORT_WORDS.search(question):
        query += EXPORT_HINT
    if CHART_WORDS.search(question):
        query += CHART_HINT
    return query


def suggestions(df: pd.DataFrame) -> list:
    """Four (label, question) chips built from the dataset's own columns."""
    n_rows = len(df)
    numeric = [c for c in df.columns if is_numeric(df[c])
               and not re.search(r"(^|_)id$|^id|index", str(c), re.I)
               and df[c].nunique() < n_rows]
    numeric.sort(key=lambda c: df[c].nunique(), reverse=True)
    categorical = [c for c in df.columns if not is_numeric(df[c]) and 1 < df[c].nunique() <= 30]
    categorical.sort(key=lambda c: df[c].nunique())

    chips = []
    n1 = numeric[0] if numeric else None
    n2 = numeric[1] if len(numeric) > 1 else n1
    c1 = categorical[0] if categorical else None
    c2 = categorical[1] if len(categorical) > 1 else c1
    if n1:
        chips.append((f"avg {n1}", f"What is the average {n1}?"))
    if n1 and c1:
        chips.append((f"{n1} by {c1}", f"Show the average {n1} grouped by {c1}"))
    if c2:
        chips.append((f"plot count by {c2}", f"Plot a bar chart of the number of rows per {c2}"))
    elif n1:
        chips.append((f"plot {n1} distribution", f"Plot a histogram of {n1}"))
    if n2:
        chips.append((f"top 5 by {n2}", f"Show the top 5 rows with the highest {n2}"))
    for fallback in [("row count", "How many rows are there?"),
                     ("missing values", "Show the number of missing values per column"),
                     ("describe", "Show summary statistics for every numeric column"),
                     ("first rows", "Show the first 10 rows")]:
        if len(chips) >= 4:
            break
        if fallback not in chips:
            chips.append(fallback)
    return chips[:4]


def friendly_error(detail) -> str:
    """Short, human error line. Never a raw traceback."""
    low = str(detail or "").lower()
    if any(k in low for k in ("api key", "api_key", "authentication", "401", "unauthorized")):
        return "llm auth failed: check the API key in secrets / .env"
    if "rate limit" in low or "429" in low:
        return "llm rate limit reached: wait a moment and retry"
    if "does not exist" in low or "notfound" in low or "model_not_found" in low:
        return f"model not found: check LLM_MODEL ({LLM_MODEL})"
    if "timeout" in low or "timed out" in low:
        return "llm request timed out: retry"
    return "could not answer: rephrase, or name the exact columns"


def to_result(response) -> dict:
    """Convert a PandasAI v2 result ({"type", "value"} via TypedResponseParser)."""
    if isinstance(response, dict):
        rtype, value = response.get("type"), response.get("value")
    else:  # v2 reports failures as a plain string instead of raising
        rtype, value = None, response

    if isinstance(value, str) and value.startswith(V2_FAILURE_PREFIX):
        return {"kind": "error", "content": friendly_error(value)}
    if rtype == "dataframe" or isinstance(value, pd.DataFrame):
        return {"kind": "dataframe", "content": pd.DataFrame(value)}
    if rtype in ("plot", "chart"):
        # Read bytes now: PandasAI reuses the same chart file path between questions.
        try:
            if isinstance(value, str) and value.startswith("data:image"):
                img = base64.b64decode(value.split(",", 1)[1])
            else:
                with open(value, "rb") as f:
                    img = f.read()
            return {"kind": "chart", "content": img}
        except Exception:
            return {"kind": "error", "content": "chart was generated but could not be loaded"}
    if rtype == "number":
        try:  # works for Python and numpy numeric types
            num = float(value)
            text = f"{int(num):,}" if num.is_integer() else f"{num:,.4f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError, OverflowError):
            return {"kind": "text", "content": str(value)}
        return {"kind": "number", "content": text, "dtype": type(value).__name__}
    return {"kind": "text", "content": str(value)}


# ---------------------------------------------------------------- exports
PDF_MAX_ROWS = 2000


def _latin1(text) -> str:
    """Core PDF fonts are latin-1 only; replace anything else."""
    return str(text).encode("latin-1", "replace").decode("latin-1")


def df_to_pdf(df: pd.DataFrame, title: str) -> bytes:
    df = df.head(PDF_MAX_ROWS)
    pdf = FPDF(orientation="L" if df.shape[1] > 5 else "P", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.multi_cell(0, 8, _latin1(title))
    pdf.ln(2)
    pdf.set_font("Helvetica", size=8)
    with pdf.table(text_align="LEFT", line_height=5) as table:
        header = table.row()
        for col in df.columns:
            header.cell(_latin1(col))
        for row in df.itertuples(index=False):
            r = table.row()
            for v in row:
                r.cell("" if pd.isna(v) else _latin1(v)[:60])
    return bytes(pdf.output())


def df_to_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def attach_exports(result: dict, question: str) -> None:
    """Pre-build download files once, so reruns don't regenerate them."""
    if result["kind"] == "dataframe":
        df = result["content"]
        result["exports"] = {
            "csv": df.to_csv(index=False).encode("utf-8"),
            "xlsx": df_to_excel(df),
            "pdf": df_to_pdf(df, question),
            "truncated": len(df) > PDF_MAX_ROWS,
        }


DOWNLOADS = {  # format -> (label, file name, mime type)
    "csv": ("csv", "result.csv", "text/csv"),
    "xlsx": ("xlsx", "result.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pdf": ("pdf", "result.pdf", "application/pdf"),
}


# ---------------------------------------------------------------- session state
ss = st.session_state
ss.setdefault("blocks", [])        # answered queries, oldest first
ss.setdefault("file_id", None)
ss.setdefault("use_sample", False)
ss.setdefault("animate", None)     # id of the block to type out once
ss.setdefault("uploader_nonce", 0)


def reset_history() -> None:
    ss.blocks = []
    ss.animate = None
    ss.in_flight = False


def queue_query(question: str) -> None:
    ss.queued = question


def load_sample() -> None:
    ss.use_sample = True


# A previous run was interrupted mid-question (stop button, or a new question sent
# while running): record that instead of leaving the question unanswered.
if ss.get("in_flight"):
    ss.in_flight = False
    ss.blocks.append({
        "id": uuid.uuid4().hex[:8], "question": ss.get("in_flight_q", ""),
        "ts": ss.get("in_flight_ts", ""), "ms": None, "code": None,
        "result": {"kind": "stopped", "content": "SIGINT: stopped by user"},
    })


# ---------------------------------------------------------------- top bar
st.markdown(
    f'<div class="topbar"><div class="wordmark"><span class="proto">EDA</span> Terminal'
    f'</div>'
    f'<div class="tags"><span class="tag">model <b>{esc(LLM_MODEL)}</b></span>'
    f'<span class="tag">pandasai <b>{esc(PANDASAI_VERSION)}</b></span>'
    f'<span class="tag">codegen <b>pandas</b></span></div></div>',
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- sidebar: session panel
with st.sidebar:
    st.markdown('<div class="side-title">session</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader("dataset", type=["csv", "xlsx", "xls"],
                                key=f"uploader_{ss.uploader_nonce}")
    if uploaded is not None:
        source_name, raw_bytes = uploaded.name, uploaded.getvalue()
    elif ss.use_sample and SAMPLE_FILE.exists():
        source_name, raw_bytes = SAMPLE_FILE.name, SAMPLE_FILE.read_bytes()
    else:
        source_name, raw_bytes = None, None

    sheet = None
    if source_name and source_name.lower().endswith(EXCEL_EXTS):
        try:
            sheets = excel_sheet_names(raw_bytes)
            sheet = st.selectbox("sheet", sheets) if len(sheets) > 1 else sheets[0]
        except Exception:
            st.markdown('<div class="err"><b>ERR:</b> could not open workbook</div>', unsafe_allow_html=True)
            source_name = raw_bytes = None

    schema_slot = st.container()
    if source_name:
        st.button("clear session", width="stretch", on_click=reset_history,
                  help="Clear the query history for this dataset")
    if APP_PASSWORD and st.button("log out", width="stretch"):
        ss.clear()
        st.rerun()
    year = datetime.date.today().year
    st.markdown(
        f'<div class="side-foot">&copy; {year} <a href="{PORTFOLIO_URL}" target="_blank" rel="noopener">'
        f'{esc(OWNER_NAME.lower())}</a> &middot; <a href="{GITHUB_URL}" target="_blank" rel="noopener">github</a>'
        f' &middot; <a href="{LINKEDIN_URL}" target="_blank" rel="noopener">linkedin</a></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- empty state
def render_empty_state() -> None:
    lines = [
        ('<span class="g">$</span> eda-terminal --init', 21, "0.2s"),
        ("&gt; waiting for dataset...", 25, "1.3s"),
        ("&gt; drop a CSV or Excel file in the session panel", 49, "2.4s"),
    ]
    rows = []
    for i, (text, n, delay) in enumerate(lines):
        dur = f"{max(0.5, n * 0.028):.2f}s"
        rows.append(f'<div class="tl" style="--n:{n};--d:{dur};--delay:{delay}">'
                    f'<span class="t">{text}</span></div>')
    st.markdown(
        '<div class="term"><div class="term-bar"><span class="dot"></span><span class="dot"></span>'
        '<span class="dot"></span><span class="title">~/eda-terminal</span></div>'
        f'<div class="term-body">{"".join(rows)}</div></div>',
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 0.9, 1])
    with mid:
        st.button("load sample: titanic.csv", type="primary", width="stretch", on_click=load_sample,
                  disabled=not SAMPLE_FILE.exists())
    st.markdown(
        '<div class="steps">'
        '<div><span class="n">01</span><b>upload</b> &rarr; a CSV or Excel file in the session panel</div>'
        '<div><span class="n">02</span><b>ask</b> &rarr; a question in plain English in the command bar</div>'
        '<div><span class="n">03</span><b>get</b> &rarr; the generated pandas code + its live output</div>'
        '</div>',
        unsafe_allow_html=True,
    )


if not source_name:
    if ss.file_id is not None:  # dataset was removed: forget it
        ss.file_id = None
        reset_history()
    render_empty_state()
    st.stop()


# ---------------------------------------------------------------- load dataset
file_id = f"{hashlib.md5(raw_bytes).hexdigest()}:{source_name}:{sheet}"
if ss.file_id != file_id:  # new file -> reset history and re-parse
    try:
        pdf = read_table(raw_bytes, source_name, sheet)
    except Exception as err:  # malformed file, empty file, etc.
        st.markdown(f'<div class="err"><b>ERR:</b> could not read {esc(source_name)}: '
                    f'{esc(str(err).splitlines()[0][:160])}</div>', unsafe_allow_html=True)
        st.stop()
    if pdf.empty:
        st.markdown(f'<div class="err"><b>ERR:</b> {esc(source_name)} has no rows</div>', unsafe_allow_html=True)
        st.stop()
    ss.file_id = file_id
    ss.pdf = pdf
    ss.sdf = make_smart_df(pdf)
    ss.chips = suggestions(pdf)
    reset_history()

pdf: pd.DataFrame = ss.pdf
sdf: SmartDataframe = ss.sdf
display_name = source_name + (f" [{sheet}]" if sheet and source_name.lower().endswith(EXCEL_EXTS) else "")

# sidebar schema "file tree"
with schema_slot:
    rows = []
    cols = list(pdf.columns)
    for i, c in enumerate(cols):
        branch = "&#9492;&#9472;" if i == len(cols) - 1 else "&#9500;&#9472;"
        dtype = str(pdf[c].dtype)
        num_cls = " dtn" if is_numeric(pdf[c]) else ""
        rows.append(f'<div class="tree-row"><span class="br">{branch}</span><span class="col" title="{esc(c)}">'
                    f'{esc(c)}</span><span class="dt{num_cls}">{esc(dtype)}</span></div>')
    st.markdown(f'<div class="side-title" style="margin-top:6px">schema</div>'
                f'<div class="tree"><div class="tree-root">&#9662; {esc(display_name)}</div>{"".join(rows)}</div>',
                unsafe_allow_html=True)

# Cmd+K / Ctrl+K focuses the command bar (script runs in a hidden component iframe).
with st.container(key="cmdk"):
    components.html(
        """<script>
        const doc = window.parent.document;
        if (!doc.__edaTerminalCmdK) {
          doc.__edaTerminalCmdK = true;
          doc.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
              const box = doc.querySelector('.st-key-cmdbar textarea');
              if (box) { e.preventDefault(); box.focus(); }
            }
          });
        }
        </script>""",
        height=0,
    )
st.markdown("<style>.st-key-cmdk {display: none;}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------- command bar + chips
with st.container(key="cmdbar"):
    typed = st.chat_input("ask your data anything...", key="cmd")

with st.container(key="chips", horizontal=True):
    st.markdown('<span class="chips-label">try</span>', unsafe_allow_html=True)
    for i, (label, question) in enumerate(ss.chips):
        st.button(label, key=f"chip_{i}", on_click=queue_query, args=(question,))

prompt = typed or ss.pop("queued", None)


# ---------------------------------------------------------------- answer blocks
BADGES = {"number": "NUMBER", "text": "TEXT", "dataframe": "TABLE", "chart": "CHART",
          "error": "ERROR", "stopped": "STOPPED"}


def block_header(question: str, meta_html: str) -> None:
    st.markdown(f'<div class="blk-head"><div class="blk-q"><span class="prompt">&gt;</span>{esc(question)}</div>'
                f'<div class="blk-meta">{meta_html}</div></div>', unsafe_allow_html=True)


def pane_head(title: str, right_html: str = "") -> None:
    st.markdown(f'<div class="pane-head"><span class="pane-title">{title}</span>{right_html}</div>',
                unsafe_allow_html=True)


def render_output(block: dict) -> None:
    res = block["result"]
    kind, content = res["kind"], res["content"]
    if kind == "number":
        st.markdown(f'<div class="num">{esc(content)}</div>'
                    f'<div class="num-label">result &middot; {esc(res.get("dtype", "number"))}</div>',
                    unsafe_allow_html=True)
    elif kind == "dataframe":
        st.dataframe(content, width="stretch", height=min(38 + 35 * len(content), 360))
        ex = res.get("exports")
        if ex:
            st.markdown(f'<div class="dl-label">export &middot; {len(content):,} rows</div>',
                        unsafe_allow_html=True)
            with st.container(horizontal=True, gap="small"):
                for fmt, (label, fname, mime) in DOWNLOADS.items():
                    st.download_button(label, ex[fmt], fname, mime, icon=":material/download:",
                                       on_click="ignore", key=f"dl_{block['id']}_{fmt}")
            if ex["truncated"]:
                st.markdown(f'<div class="num-label">pdf holds the first {PDF_MAX_ROWS:,} rows</div>',
                            unsafe_allow_html=True)
    elif kind == "chart":
        st.image(content, width="stretch")
        st.download_button("png", content, "chart.png", "image/png", icon=":material/download:",
                           on_click="ignore", key=f"dl_{block['id']}_png")
    elif kind == "error":
        st.markdown(f'<div class="err"><b>ERR:</b> {esc(content)}</div>', unsafe_allow_html=True)
    elif kind == "stopped":
        st.markdown(f'<div class="sigint">{esc(content)}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="str">{esc(content)}</div>', unsafe_allow_html=True)


def render_block(block: dict, newest: bool, animate: bool) -> None:
    res = block["result"]
    ms = f' &middot; <b>{block["ms"]:,} ms</b>' if block.get("ms") is not None else ""
    with st.container(border=True, key=f"blk_{block['id']}"):
        block_header(block["question"], f'{esc(block["ts"])}{ms}')
        left, right = st.columns([55, 45], gap="medium")
        code = block.get("code")
        with left:
            n_lines = len(code.splitlines()) if code else 0
            pane_head("code", f'<span class="blk-meta">pandas &middot; {n_lines} lines</span>' if code else "")
            with st.container(key=f"code_{block['id']}"):
                if not code:
                    st.markdown('<div class="nocode"># no code was generated for this query</div>',
                                unsafe_allow_html=True)
                elif animate:  # type the code out line by line, once
                    lines = code.splitlines()
                    slot = st.empty()
                    delay = min(0.05, 1.6 / max(len(lines), 1))
                    for i in range(1, len(lines) + 1):
                        slot.code("\n".join(lines[:i]), language="python", line_numbers=True, wrap_lines=True)
                        time.sleep(delay)
                else:
                    st.code(code, language="python", line_numbers=True, wrap_lines=True)
        with right:
            badge_cls = res["kind"] if res["kind"] in ("error", "stopped") else ""
            pane_head("output", f'<span class="badge {badge_cls}">{BADGES.get(res["kind"], "TEXT")}</span>')
            render_output(block)


def run_in_background(df: SmartDataframe, query: str, question: str) -> dict:
    """Run df.chat() in a thread; returns a dict the thread fills in."""
    box: dict = {}

    def worker():
        # PandasAI v2 records code on the agent via callbacks. Clear it first so a
        # failed question can't show the previous question's code. (In 2.3.2,
        # SmartDataframe.last_code_generated returns the executed code, so read
        # both values from the agent directly.)
        agent = df._agent
        agent.last_code_generated = agent.last_code_executed = None
        start = time.perf_counter()
        for attempt in range(2):  # LLMs occasionally reply without a code block: retry once
            try:
                result = to_result(df.chat(query))
            except Exception as err:
                traceback.print_exc()
                result = {"kind": "error", "content": friendly_error(err)}
            got_code = agent.last_code_executed or agent.last_code_generated
            retryable = result["kind"] == "error" and not got_code \
                and result["content"].startswith("could not answer")
            if not retryable:
                break
        attach_exports(result, question)
        box["ms"] = int((time.perf_counter() - start) * 1000)
        box["code"] = agent.last_code_executed or agent.last_code_generated
        box["result"] = result

    threading.Thread(target=worker, daemon=True).start()
    return box


job = None
if prompt:
    ss.in_flight, ss.in_flight_q = True, prompt
    ss.in_flight_ts = datetime.datetime.now().strftime("%H:%M:%S")
    job = run_in_background(sdf, build_query(prompt), prompt)
    with st.container(border=True, key="running"):
        block_header(prompt, f"{ss.in_flight_ts} &middot; <b>running</b>")
        run_status = st.empty()
        # Clicking stop triggers a rerun; Streamlit interrupts the wait loop below at
        # its next st.* call, and the in_flight flag records the stop on rerun.
        st.button("stop", key="stop_query", type="primary", icon=":material/stop:")

blocks = list(reversed(ss.blocks))  # newest first
for i, block in enumerate(blocks):
    animate = block["id"] == ss.animate
    if animate:
        ss.animate = None  # animate exactly once, even if this run gets interrupted
    render_block(block, newest=(i == 0 and job is None), animate=animate)

if not blocks and job is None:
    st.markdown('<div class="str m" style="margin-top:6px">no queries yet. type a question above, '
                'or click a suggestion.</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------- status bar
nulls = int(pdf.isna().sum().sum())
n_num = sum(is_numeric(pdf[c]) for c in pdf.columns)
mem = human_bytes(pdf.memory_usage(deep=True).sum())
mode = "RUN" if job else "READY"
with st.container(key="statusbar"):
    st.markdown(
        f'<div class="statusbar"><span class="mode">{mode}</span><b>{esc(display_name)}</b>'
        f' &middot; <b>{len(pdf):,}</b> rows &middot; <b>{pdf.shape[1]}</b> cols'
        f' &middot; <b>{nulls:,}</b> nulls &middot; <b>{n_num}</b> numeric / <b>{pdf.shape[1] - n_num}</b> categorical'
        f' &middot; <b>{mem}</b></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- wait for the running query
if job is not None:
    start = time.time()
    while "result" not in job:
        run_status.markdown(f'<div class="running-line">executing pandas &middot; {time.time() - start:0.1f}s'
                            f'</div>', unsafe_allow_html=True)
        time.sleep(0.2)
    new_block = {"id": uuid.uuid4().hex[:8], "question": prompt, "ts": ss.in_flight_ts,
                 "ms": job["ms"], "code": job["code"], "result": job["result"]}
    ss.blocks.append(new_block)
    ss.animate = new_block["id"]
    ss.in_flight = False
    st.rerun()
