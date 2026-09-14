from collections import Counter
from datetime import datetime, timedelta

import streamlit as st

from memory_store import load_mistake_records
from ui import page_banner


page_banner("成长看板", "所有指标来自当前 SQLite 错题本；数据不足时不生成演示趋势。")
records = load_mistake_records()
now = datetime.now()
recent = [r for r in records if datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S") >= now - timedelta(days=30)]
counts = Counter(issue["issue_type"] for r in records for issue in r["issues"])
top_issue = counts.most_common(1)[0][0] if counts else "暂无"
repeated = sum(count > 1 for count in counts.values())
recurrence = repeated / len(counts) if counts else 0

cols = st.columns(4)
cols[0].metric("总复盘数", len(records), border=True)
cols[1].metric("近 30 天复盘", len(recent), border=True)
cols[2].metric("高频错误类型", top_issue, border=True)
cols[3].metric("重复错误类型占比", f"{recurrence:.0%}", border=True,
               help="出现超过一次的错误类型占全部错误类型的比例。")

left, right = st.columns([1.6, 1], gap="medium")
with left.container(border=True):
    st.subheader("复盘趋势")
    if records:
        by_day = Counter(r["created_at"][:10] for r in records)
        st.line_chart({"复盘数": dict(sorted(by_day.items()))}, x_label="日期", y_label="记录数")
    else:
        st.info("保存复盘后才会形成趋势。")
with right.container(border=True):
    st.subheader("错误类型分布")
    if counts:
        st.bar_chart(dict(counts.most_common()), horizontal=True, x_label="次数", y_label="错误类型")
    else:
        st.info("当前没有结构化错误标签。")

st.subheader("最近记录")
if records:
    st.dataframe([{"时间": r["created_at"], "棋盘": f"{r['board_size']} 路",
                   "错误数": len(r["issues"]), "摘要": r["position_summary"]} for r in records[:8]], hide_index=True)
else:
    st.caption("暂无真实记录。")
