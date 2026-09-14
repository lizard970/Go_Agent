import streamlit as st

from memory_store import load_history
from ui import page_banner


page_banner("开始一次复盘", "从棋盘截图或 SGF 棋谱进入真实分析流程。")
history = load_history()
left, middle, right = st.columns([1, 1, 1.1], gap="medium")
with left.container(border=True):
    st.subheader("图片复盘")
    st.write("识别棋盘、人工核对，再生成 GPT 复盘并可加入错题本。")
    if st.button("上传棋盘截图", type="primary", width="stretch"):
        st.session_state["review_input"] = "图片"
        st.switch_page("app_pages/review.py")
with middle.container(border=True):
    st.subheader("SGF 复盘")
    st.write("选择任意手数，调用本机 KataGo 获取胜率、目差、候选着与 PV。")
    if st.button("上传 SGF 棋谱", type="primary", width="stretch"):
        st.session_state["review_input"] = "SGF"
        st.switch_page("app_pages/review.py")
with right.container(border=True):
    st.subheader("真实记录")
    st.metric("错题本记录", len(history), border=True)
    if history:
        latest = history[0]
        st.caption(f"最近保存：{latest['created_at']} · {latest['board_size']} 路")
        st.write(latest["position_summary"])
    else:
        st.info("还没有保存的错题。")

st.subheader("最近复盘")
if history:
    st.dataframe([{"保存时间": item["created_at"], "棋盘": f"{item['board_size']} 路",
                   "执棋": "黑" if item["user_color"] == "black" else "白",
                   "局面摘要": item["position_summary"]} for item in history[:5]], hide_index=True)
else:
    st.caption("完成一次图片深度复盘并加入错题本后，这里会显示真实记录。")
