"""
W2 walking-skeleton frontend (blueprint §9.1).

Question box -> narrative -> chart -> SQL -> data table. Thin wrapper over
agent.pipeline.ask; the V2 Next.js+FastAPI path reuses the same agent surface.

Run:  .venv/Scripts/streamlit.exe run web/app.py
"""
from __future__ import annotations

import pathlib
import sys

import streamlit as st

# make the `agent` package importable when launched via `streamlit run`
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agent.pipeline import ask  # noqa: E402

st.set_page_config(page_title="投研数据智能体", layout="wide")
st.title("投研数据智能体")
st.caption("中国公募基金 · 自然语言问答 (W2 骨架)")

with st.form("ask"):
    question = st.text_input("提问", "易方达蓝筹精选今年的单位净值走势")
    submitted = st.form_submit_button("分析")

if submitted and question.strip():
    with st.spinner("思考中…"):
        try:
            res = ask(question)
        except Exception as e:
            st.error(f"出错了：{e}")
            st.stop()

    st.markdown("### 分析")
    st.write(res.narrative)

    if res.figure.data:
        st.plotly_chart(res.figure, use_container_width=True)
    else:
        st.info("本次结果未生成图表，请见下方数据。")

    with st.expander("生成的 SQL"):
        st.code(res.sql, language="sql")
    with st.expander(f"原始数据（{len(res.df)} 行）"):
        st.dataframe(res.df, use_container_width=True)
