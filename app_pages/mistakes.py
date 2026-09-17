from collections import Counter

import matplotlib.pyplot as plt
import streamlit as st

from memory_store import load_mistake_game_context, load_mistake_records
from ui import draw_board, page_banner


page_banner("错题本", "筛选并查看 SQLite 中真实保存的复盘记录。")
records = load_mistake_records()
if not records:
    st.info("错题本为空。完成一次图片深度复盘后，记录会显示在这里。")
    st.stop()

query = st.text_input("搜索错误描述或关键词", placeholder="例如：断点、死活、方向错误")
issue_types = sorted({issue["issue_type"] for record in records for issue in record["issues"]})
selected_types = st.multiselect("错误类型", issue_types, placeholder="全部类型")
frequencies = Counter(issue["issue_type"] for record in records for issue in record["issues"])


def matches(record):
    text = " ".join([record["position_summary"], record["high_commentary"],
                     *(issue["issue_type"] + " " + issue["evidence"] for issue in record["issues"])]).lower()
    return (not query or query.lower() in text) and (not selected_types or any(
        issue["issue_type"] in selected_types for issue in record["issues"]))


records = [record for record in records if matches(record)]
sort = st.segmented_control("排序", ["最近新增", "重复最多"], default="最近新增", required=True)
if sort == "重复最多":
    records.sort(key=lambda record: max((frequencies[i["issue_type"]] for i in record["issues"]), default=0), reverse=True)

selected_id = st.session_state.get("selected_mistake")
for record in records:
    selected = record["id"] == selected_id
    style = "selected" if selected else ("dim" if selected_id is not None else "normal")
    with st.container(border=True, key=f"mistake_{style}_{record['id']}"):
        summary, date, action = st.columns([4, 1.2, .8], vertical_alignment="center")
        summary.markdown(f"**{record['position_summary']}**")
        tags = " · ".join(issue["issue_type"] for issue in record["issues"]) or "未标注错误"
        summary.caption(f"{record['board_size']} 路 · {'黑棋' if record['user_color'] in ('black', 'B') else '白棋'} · {tags}")
        date.caption(record["created_at"])
        if action.button("收起" if selected else "查看", key=f"select_mistake_{record['id']}", width="stretch"):
            st.session_state["selected_mistake"] = None if selected else record["id"]
            st.rerun()
        if selected:
            board, explanation, evidence = st.columns([.75, 1.35, 1], gap="medium")
            if record["board_data"] is not None:
                fig = draw_board(record["board_data"]["board_size"], record["board_data"]["stones"], size=2.4)
                board.pyplot(fig, width="stretch")
                plt.close(fig)
            else:
                board.caption("该 SGF 错题保存的是事件级证据，未单独保存棋盘截图。")
            explanation.markdown("**深度点评**")
            explanation.write(record["high_commentary"])
            evidence.markdown("**结构化错误**")
            for issue in record["issues"]:
                evidence.markdown(f"**{issue['issue_type']}** · {issue['severity']}  \n{issue['evidence']}  \n改进：{issue['improvement']}")
            if record.get("game_id") and st.button(
                "回到棋局查看", key=f"open_game_{record['id']}", type="primary"
            ):
                context = load_mistake_game_context(record["id"])
                if context is None or context.get("source_content") is None:
                    st.error("无法读取这条错题的原始棋谱。")
                else:
                    st.session_state["workspace_mistake_context"] = context
                    cached = context.get("review_run")
                    if cached is not None:
                        st.session_state["agent_home_review_run"] = cached
                        st.session_state[
                            f"workspace_agent_move_{cached.result.state.game_id}"
                        ] = context["worst_move_number"]
                    else:
                        st.session_state.pop("agent_home_review_run", None)
                    st.session_state["workspace_ignore_agent_cache"] = False
                    st.session_state["review_input"] = "SGF"
                    st.switch_page("app_pages/review.py")

if not records:
    st.info("没有符合当前筛选条件的记录。")
