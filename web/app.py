"""
Streamlit frontend — deployable and access-controlled.

Thin wrapper over agent.pipeline.ask. Login + role-based access via
streamlit-authenticator; secrets (DB url, LLM keys, accounts) come from
st.secrets so the same code runs locally and on Streamlit Community Cloud.

Roles:
  admin  — narrative + chart + generated SQL + full data table
  viewer — narrative + chart + truncated data; SQL hidden

The agent core (agent.pipeline.ask) stays UI-agnostic: when we later move to a
FastAPI + React canvas, authorization moves to the API boundary and this file is
replaced wholesale — the reusable assets (hosted DB, secrets, accounts) carry over.

Run locally:  streamlit run web/app.py
"""
from __future__ import annotations

import os
import pathlib
import sys

import streamlit as st

# --- 1. secrets -> env, so agent/* (which read os.environ) work unchanged ------
def _load_secrets_into_env() -> None:
    try:
        for k, v in st.secrets.items():
            if isinstance(v, str) and not os.environ.get(k):
                os.environ[k] = v
    except Exception:
        pass  # no secrets file locally → rely on .env


_load_secrets_into_env()

# make the `agent` package importable when launched via `streamlit run`
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit_authenticator as stauth  # noqa: E402

from agent.pipeline import (  # noqa: E402
    ask, build_chart, is_chart_edit, parse_chart_edit, render_spec,
)

st.set_page_config(page_title="投研数据智能体", layout="wide")


# --- 2. auth -----------------------------------------------------------------
def _build_auth():
    """Build (authenticator, role_map) from st.secrets['auth']."""
    auth = st.secrets["auth"].to_dict()
    users = auth["credentials"]["usernames"]
    # strip our custom 'role' key out of what we hand the library
    credentials = {"usernames": {
        u: {k: val for k, val in info.items() if k != "role"}
        for u, info in users.items()
    }}
    roles = {u: info.get("role", "viewer") for u, info in users.items()}
    authenticator = stauth.Authenticate(
        credentials, "invest_agent_auth",
        st.secrets.get("cookie_key", "dev-cookie-key"),
        cookie_expiry_days=7, auto_hash=False,
    )
    return authenticator, roles


try:
    authenticator, ROLE_MAP = _build_auth()
except Exception:
    st.error("未配置登录凭据。请在 .streamlit/secrets.toml 或 Cloud Secrets 中按 "
             "secrets.toml.example 填写 [auth] 段。")
    st.stop()

authenticator.login(location="main")
status = st.session_state.get("authentication_status")
if status is False:
    st.error("用户名或密码错误。")
    st.stop()
if status is None:
    st.warning("请登录以使用投研数据智能体。")
    st.stop()

username = st.session_state.get("username", "")
name = st.session_state.get("name", username)
role = ROLE_MAP.get(username, "viewer")

with st.sidebar:
    st.markdown(f"**{name}**")
    st.caption(f"身份：{'管理员' if role == 'admin' else '访客'}（{role}）")
    authenticator.logout("退出登录", location="sidebar")

# --- 3. app (stateful: new query vs in-place chart edit) ---------------------
st.title("投研数据智能体")
st.caption("中国公募基金 · 自然语言问答")

ss = st.session_state

with st.form("ask"):
    question = st.text_input("提问 / 改图指令", "易方达蓝筹精选今年的单位净值走势")
    submitted = st.form_submit_button("发送")

if submitted and question.strip():
    if is_chart_edit(question):
        if ss.get("last_df") is not None:
            ov = dict(ss.get("chart_overrides", {}))
            ov.update(parse_chart_edit(question))   # merge: color + type accumulate
            ss["chart_overrides"] = ov
        else:
            st.warning("请先生成一张图，再用「改成红色 / 换柱状图」这类指令就地修改。")
    else:
        with st.spinner("思考中…"):
            try:
                res = ask(question)
            except Exception as e:
                st.error(f"出错了：{e}")
                st.stop()
        ss["last_df"] = res.df
        ss["last_question"] = question
        ss["last_sql"] = res.sql
        ss["last_narrative"] = res.narrative
        ss["last_spec"] = res.spec            # Tier-3 spec (None → Tier-2 fallback)
        ss["chart_overrides"] = {}            # reset edits for the new chart

# render current state every run (so an edit re-renders without re-querying)
if ss.get("last_df") is not None:
    df, q = ss["last_df"], ss["last_question"]
    ov = ss.get("chart_overrides", {})
    spec = ss.get("last_spec")

    st.markdown("### 分析")
    st.write(ss["last_narrative"])

    # edits patch the spec (Tier-3); fall back to Tier-2 build_chart if no spec
    # or if the patched spec fails to render.
    fig = None
    if spec:
        s = dict(spec)
        if ov.get("chart_type"):
            s["chart_type"] = ov["chart_type"]
        try:
            fig = render_spec(df, s, recolor=ov.get("color"))
        except Exception:
            fig = None
    if fig is None:
        fig = build_chart(df, q, chart_type=ov.get("chart_type"), color=ov.get("color"))
    if fig.data:
        st.plotly_chart(fig, width="stretch")
        if ov:
            bits = []
            if ov.get("chart_type"):
                bits.append(f"图型→{ov['chart_type']}")
            if ov.get("color"):
                bits.append(f"颜色→{ov['color']}")
            st.caption("🎨 已就地修改：" + "，".join(bits) + "（基于同一份数据，未重新查询）")
    else:
        st.info("本次结果未生成图表，请见下方数据。")

    if role == "admin":
        with st.expander("生成的 SQL"):
            st.code(ss["last_sql"], language="sql")
        with st.expander(f"原始数据（{len(df)} 行）"):
            st.dataframe(df, width="stretch")
    else:
        with st.expander(f"数据（前 50 行，共 {len(df)} 行）"):
            st.dataframe(df.head(50), width="stretch")
