from datetime import datetime, timedelta

import streamlit as st

from memory_store import load_dashboard_summary
from ui import page_banner


def local_created_at(value):
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


page_banner("成长看板", "按已分析棋局和结构化错题分别统计，不使用自然语言标题充当错误类型。")
summary = load_dashboard_summary()
now = datetime.now()
recent_count = sum(
    local_created_at(value) >= now - timedelta(days=30)
    for value in summary["game_dates"]
)

cols = st.columns(3)
cols[0].metric("总复盘数", summary["total_reviews"], border=True)
cols[1].metric("近 30 天复盘", recent_count, border=True)
cols[2].metric("累计错题数", summary["mistake_count"], border=True)

left, right = st.columns([1.6, 1], gap="medium")
with left.container(border=True):
    st.subheader("复盘趋势")
    if summary["games_by_date"]:
        st.line_chart(
            {"完成棋局数": summary["games_by_date"]},
            x_label="日期", y_label="棋局数",
        )
    else:
        st.info("完成棋局分析后才会形成趋势。")
with right.container(border=True):
    st.subheader("错题阶段分布")
    if summary["phase_counts"]:
        labels = {"opening": "布局", "middlegame": "中盘", "endgame": "官子"}
        values = {
            labels.get(phase, phase): count
            for phase, count in summary["phase_counts"].items()
        }
        st.bar_chart(values, horizontal=True, x_label="错题数", y_label="阶段")
    else:
        st.info("当前没有结构化 ErrorEvent。")

st.caption(
    "总复盘数和趋势按不同棋局去重；累计错题数与阶段分布按已保存 ErrorEvent 统计。"
)
