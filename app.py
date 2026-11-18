"""Chat with any CSV using PandasAI v3 + Streamlit."""

import base64
import datetime
import hashlib
import io
import os
import re
import threading
import time
import traceback

import pandas as pd
import pandasai as pai
import streamlit as st
from dotenv import load_dotenv
from fpdf import FPDF
from pandasai_litellm import LiteLLM

load_dotenv(override=True)  # always pick up the latest .env values on rerun

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1-mini")

# ---- Branding (edit these) ----
OWNER_NAME = "Zia Ahmed"
PORTFOLIO_URL = "https://ziaahmed.dev"
GITHUB_URL = "https://github.com/ziaahmedf17"
LINKEDIN_URL = "https://www.linkedin.com/in/ziaahmedf17/"

st.set_page_config(page_title="Chat with your CSV", page_icon="📊", layout="wide")

# Hide Streamlit's Deploy button (also disabled via .streamlit/config.toml).
st.markdown(
    """
    <style>
      :root {--accent: #BFF747; --bg: #1A1A1A; --bg-2: #242424; --muted: #888888;}
      .stAppDeployButton, [data-testid="stAppDeployButton"] {display: none !important;}
      [data-testid="stSidebar"] {background: #141414; border-right: 1px solid #2a2a2a;}
      [data-testid="stCaptionContainer"], .stCaption {color: var(--muted) !important;}
      h1 {letter-spacing: -.02em;}
      [data-testid="stMetricValue"] {color: var(--accent);}
      /* Buttons: lime outline, fill on hover (portfolio style) */
      .stButton > button, .stDownloadButton > button {
        border: 1px solid var(--accent); color: var(--accent); background: transparent;
        border-radius: 999px; transition: all .2s ease;
      }
      .stButton > button:hover, .stDownloadButton > button:hover {
        background: var(--accent); color: #000; border-color: var(--accent);
      }
      .dl-label {font-size: .75rem; text-transform: uppercase; letter-spacing: .08em;
                 color: var(--muted); margin: .5rem 0 -.4rem;}
      .stDownloadButton > button {padding: .35rem 1rem; font-weight: 600; gap: .4rem;}
      .stDownloadButton > button:hover {transform: translateY(-1px);
                 box-shadow: 0 6px 18px rgba(191, 247, 71, .25);}
      .stButton > button[kind="primary"] {background: var(--accent); color: #000; border-color: var(--accent);}
      .stButton > button[kind="primary"]:hover {background: #d4ff6e; color: #000;}
      /* Chat avatars */
      [data-testid="stChatMessageAvatarUser"] {background: #2e2e2e; color: var(--accent);}
      [data-testid="stChatMessageAvatarAssistant"] {background: var(--accent); color: #000;}
      [data-testid="stChatMessage"] {background: var(--bg-2); border-radius: 14px;}
      [data-testid="stChatInput"] textarea:focus {caret-color: var(--accent);}
      .app-footer {text-align: center; color: var(--muted); font-size: .85rem; padding: 1.5rem 0 .5rem;}
      .app-footer a {color: var(--accent) !important; text-decoration: none; margin: 0 .35rem;}
      .app-footer a:hover {text-decoration: underline;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- LLM setup
@st.cache_resource(show_spinner=False)
def configure_llm(model: str) -> None:
    """Register the LiteLLM-backed model with PandasAI (once per process)."""
    pai.config.set({"llm": LiteLLM(model=model), "verbose": False})


configure_llm(LLM_MODEL)


# ---------------------------------------------------------------- data loading
def read_csv_with_fallback(raw: bytes) -> pd.DataFrame:
    """Read CSV bytes, trying utf-8 first and falling back to latin-1."""
    last_err = None
    for encoding in ("utf-8", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=encoding)
        except UnicodeDecodeError as err:
            last_err = err
    raise last_err  # latin-1 decodes any byte, so this is practically unreachable


def reset_chat() -> None:
    st.session_state.messages = []
    st.session_state.in_flight = False


st.session_state.setdefault("messages", [])
st.session_state.setdefault("file_id", None)

st.title("📊 Chat with your CSV")
st.caption(f"Powered by PandasAI v3 · model: `{LLM_MODEL}`")

def render_footer() -> None:
    year = datetime.date.today().year
    st.markdown(
        f"""<div class="app-footer">© {year} <a href="{PORTFOLIO_URL}" target="_blank"
        rel="noopener">{OWNER_NAME}</a>. All rights reserved.<br>
        <a href="{GITHUB_URL}" target="_blank" rel="noopener">GitHub</a> ·
        <a href="{LINKEDIN_URL}" target="_blank" rel="noopener">LinkedIn</a></div>""",
        unsafe_allow_html=True,
    )


with st.sidebar:
    uploaded = st.file_uploader("Upload a CSV file", type=["csv"])
    if st.button("Clear chat", width="stretch"):
        reset_chat()
    st.caption(
        "Tip: ask for a table (e.g. *“show name and survived columns”*) "
        "and download it as CSV, Excel or PDF."
    )
    render_footer()

if uploaded is None:
    st.info("Upload a CSV file in the sidebar to get started.")
    render_footer()
    st.stop()

raw_bytes = uploaded.getvalue()
file_id = hashlib.md5(raw_bytes).hexdigest() + uploaded.name

# New file -> reset chat history and re-parse.
if st.session_state.file_id != file_id:
    try:
        pdf = read_csv_with_fallback(raw_bytes)
    except Exception as err:  # malformed CSV, empty file, etc.
        st.error(f"Could not read `{uploaded.name}` as CSV: {err}")
        st.stop()
    st.session_state.file_id = file_id
    st.session_state.pdf = pdf
    st.session_state.sdf = pai.DataFrame(pdf)
    reset_chat()

pdf: pd.DataFrame = st.session_state.pdf
sdf = st.session_state.sdf

# ---------------------------------------------------------------- file summary
c1, c2, c3 = st.columns(3)
c1.metric("File", uploaded.name)
c2.metric("Rows", f"{pdf.shape[0]:,}")
c3.metric("Columns", f"{pdf.shape[1]:,}")
with st.expander("Preview (first 10 rows)", expanded=True):
    st.dataframe(pdf.head(10), width="stretch")


# ---------------------------------------------------------------- chat helpers
def to_message(response) -> dict:
    """Convert a PandasAI response into a serialisable chat message."""
    rtype = getattr(response, "type", None)
    value = getattr(response, "value", response)

    if rtype == "error":
        return {"kind": "error", "content": friendly_error(getattr(response, "error", None))}
    if rtype == "dataframe" or isinstance(value, pd.DataFrame):
        return {"kind": "dataframe", "content": pd.DataFrame(value)}
    if rtype == "chart":
        # Read bytes now: PandasAI reuses the same chart file path between questions.
        try:
            if isinstance(value, str) and value.startswith("data:image"):
                img = base64.b64decode(value.split(",", 1)[1])
            else:
                with open(value, "rb") as f:
                    img = f.read()
            return {"kind": "chart", "content": img}
        except Exception:
            return {"kind": "error", "content": "A chart was generated but could not be loaded."}
    if rtype == "number":
        try:  # works for Python and numpy numeric types
            num = float(value)
            text = f"{int(num):,}" if num.is_integer() else f"{num:,.4f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError, OverflowError):
            text = str(value)
        return {"kind": "text", "content": text}
    return {"kind": "text", "content": str(value)}


def friendly_error(detail) -> str:
    detail = str(detail or "")
    low = detail.lower()
    if "api key" in low or "api_key" in low or "authentication" in low or "401" in low:
        return (
            "⚠️ I couldn't reach the language model — the API key looks missing or invalid. "
            "Check your `.env` file and restart the app."
        )
    if "rate limit" in low or "429" in low:
        return "⚠️ The language model is rate-limiting requests. Please wait a moment and try again."
    return (
        "⚠️ Sorry, I couldn't answer that one. Try rephrasing the question, "
        "or mention the exact column names you're interested in."
    )


# ---------------------------------------------------------------- exports
PDF_MAX_ROWS = 2000
EXPORT_WORDS = re.compile(r"\b(pdf|csv|excel|xlsx|xls|download|export|save|file)\b", re.I)
EXPORT_HINT = (
    "\n\n(Note: return the requested data as a dataframe result. "
    "Do not write any files or import extra libraries — the app handles file downloads.)"
)


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


def attach_exports(msg: dict, question: str) -> None:
    """Pre-build download files once, so reruns don't regenerate them."""
    if msg["kind"] == "dataframe":
        df = msg["content"]
        msg["exports"] = {
            "csv": df.to_csv(index=False).encode("utf-8"),
            "xlsx": df_to_excel(df),
            "pdf": df_to_pdf(df, question),
            "truncated": len(df) > PDF_MAX_ROWS,
        }


DOWNLOADS = {  # format -> (label, file name, mime type, Material icon)
    "csv": ("CSV", "result.csv", "text/csv", ":material/csv:"),
    "xlsx": ("Excel", "result.xlsx",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ":material/table_view:"),
    "pdf": ("PDF", "result.pdf", "application/pdf", ":material/picture_as_pdf:"),
}


def download_buttons(msg: dict, key: str) -> None:
    # on_click="ignore": downloading doesn't rerun the app (or interrupt a running question).
    if msg["kind"] == "dataframe" and "exports" in msg:
        ex = msg["exports"]
        st.markdown('<div class="dl-label">Download result</div>', unsafe_allow_html=True)
        with st.container(horizontal=True, gap="small"):
            for fmt, (label, fname, mime, icon) in DOWNLOADS.items():
                st.download_button(label, ex[fmt], fname, mime, icon=icon,
                                   on_click="ignore", key=f"{key}-{fmt}")
        if ex["truncated"]:
            st.caption(f"PDF includes the first {PDF_MAX_ROWS:,} rows; CSV/Excel include all rows.")
    elif msg["kind"] == "chart":
        st.download_button("Download PNG", msg["content"], "chart.png", "image/png",
                           icon=":material/image:", on_click="ignore", key=f"{key}-png")


def render(msg: dict, key: str = "") -> None:
    kind, content = msg["kind"], msg["content"]
    if kind == "dataframe":
        st.dataframe(content, width="stretch")
        download_buttons(msg, key)
    elif kind == "chart":
        st.image(content)
        download_buttons(msg, key)
    elif kind == "error":
        st.warning(content)
    elif kind == "stopped":
        st.caption(content)
    else:
        st.markdown(content)


# ---------------------------------------------------------------- chat UI
def run_chat_in_background(df, query: str, question: str) -> dict:
    """Start df.chat() in a thread; returns a dict the thread fills in."""
    box: dict = {}

    def worker():
        try:
            msg = to_message(df.chat(query))
            attach_exports(msg, question)
        except Exception as err:
            traceback.print_exc()
            msg = {"kind": "error", "content": friendly_error(err)}
        box["answer"] = msg

    threading.Thread(target=worker, daemon=True).start()
    return box


# A previous run was interrupted mid-question (Stop button, or a new question
# sent while thinking): record that instead of leaving the question unanswered.
if st.session_state.get("in_flight"):
    st.session_state.in_flight = False
    st.session_state.messages.append(
        {"role": "assistant", "kind": "stopped", "content": ":material/stop_circle: Stopped. Ask another question whenever you're ready."}
    )

for i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        render(msg, key=f"m{i}")

if prompt := st.chat_input("Ask a question about your data…"):
    user_msg = {"role": "user", "kind": "text", "content": prompt}
    st.session_state.messages.append(user_msg)
    with st.chat_message("user"):
        render(user_msg)

    with st.chat_message("assistant"):
        # Generated code can't create downloadable files, so steer export
        # requests toward a dataframe and let the app build the file.
        query = prompt + EXPORT_HINT if EXPORT_WORDS.search(prompt) else prompt
        st.session_state.in_flight = True
        box = run_chat_in_background(sdf, query, prompt)

        status = st.empty()
        # Clicking Stop triggers a rerun; Streamlit interrupts this loop at the
        # next st.* call, and the in_flight flag records the stop on rerun.
        st.button("Stop", key="stop_query", icon=":material/stop_circle:", type="primary")
        start = time.time()
        while "answer" not in box:
            status.markdown(f"⏳ Thinking… {int(time.time() - start)}s")
            time.sleep(0.25)
        status.empty()

        answer = box["answer"]
        answer["role"] = "assistant"
        st.session_state.messages.append(answer)
        st.session_state.in_flight = False
    st.rerun()  # redraw cleanly (removes the Stop button)
