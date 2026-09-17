from dataclasses import asdict

import streamlit as st

from agent_home import (build_review_request, latest_review_run,
                        run_review_request, save_review_run)
from ui import page_banner


RESULT_KEY = "agent_home_review_run"
SAVE_KEY = "agent_home_save_observations"


def render_history(state):
    summary = state.history_summary or {}
    signal_count = summary.get("key_errors_with_history_signal", 0)
    candidate_count = summary.get("historical_candidate_count", 0)
    explicit_no_match = (
        "历史" in state.user_request
        and summary.get("status") != "unavailable"
        and not signal_count
    )
    if signal_count:
        st.info(
            f"{signal_count} 个问题找到 {candidate_count} 条较强的潜在相似记录。"
            "相似度只表示检索候选，不代表已经确认是同一种错误。"
        )
    elif explicit_no_match:
        st.caption("历史记录中暂未找到达到当前阈值的有意义相似候选。")


def render_result(run):
    result = run.result
    state = result.state
    st.subheader("复盘结果")
    if result.status == "controller_error":
        st.error(result.answer)
    elif result.status == "partial":
        st.warning(result.answer)
    elif result.answer:
        st.markdown(result.answer)
    else:
        st.info("本次复盘没有返回可展示的结果。")

    for index, evidence in enumerate(state.deep_evidence):
        explanation = (
            state.explanation_results[index]
            if index < len(state.explanation_results) else None
        )
        with st.container(border=True):
            st.markdown(
                f"**第 {evidence.worst_move_number} 手 · "
                f"{'黑棋' if evidence.player == 'B' else '白棋'} · "
                f"{evidence.phase}**"
            )
            st.caption(
                f"实战 {evidence.actual_move} · 推荐 {evidence.deep_best_move_before} · "
                f"胜率损失 {evidence.player_winrate_loss:.1%} · "
                f"目差损失 {evidence.player_score_loss:.1f}"
            )
            if explanation is not None and explanation.status == "succeeded":
                item = explanation.explanation
                st.markdown(f"**{item.title}**\n\n{item.summary}")
                st.write(f"为什么重要：{item.why_it_matters}")
                st.write(f"更好的思路：{item.better_plan}")
                st.write(f"复用要点：{item.learning_point}")
            else:
                code = explanation.error_code if explanation is not None else "not_attempted"
                st.warning(f"解释暂不可用（{code}），KataGo 证据已保留。")

    render_history(state)

    if st.button(
        "保存到错题本",
        key="agent_home_save",
    ):
        with st.spinner("正在保存…"):
            st.session_state[SAVE_KEY] = save_review_run(run)
        st.rerun()
    save_result = st.session_state.get(SAVE_KEY)
    if save_result is not None:
        if save_result["status"] == "saved":
            st.success(f"保存成功：已写入 {len(save_result['event_ids'])} 条错题记录。")
        elif save_result["status"] == "already_saved":
            st.info("已经保存。")
        elif save_result["status"] == "not_saveable":
            st.warning("当前这一手不是可保存的用户错误事件。")
        else:
            st.error(f"保存失败：{save_result.get('error', '未知错误')}")

    with st.expander("运行信息"):
        one, two, three = st.columns(3)
        one.metric("Controller", state.controller_calls)
        two.metric("Explanation", state.explanation_calls)
        three.metric("Embedding", state.embedding_calls)
        st.markdown("**执行轨迹**")
        if state.trace:
            st.dataframe([asdict(entry) for entry in state.trace], hide_index=True, width="stretch")
        else:
            st.caption("没有可用的执行轨迹。")


page_banner("围棋复盘 Agent", "上传 SGF，用一句话说明你想解决的问题。")

if RESULT_KEY not in st.session_state:
    try:
        restored = latest_review_run()
    except Exception:
        restored = None
    if restored is not None:
        st.session_state[RESULT_KEY] = restored

with st.form("agent_home_form"):
    uploaded = st.file_uploader("上传 SGF 棋谱", type=["sgf"], key="agent_home_sgf_upload")
    color_col, target_col = st.columns(2)
    user_color = color_col.segmented_control(
        "你执哪一方", ["B", "W"], default="B", required=True,
        format_func=lambda color: "黑棋" if color == "B" else "白棋",
        key="agent_home_user_color",
    )
    target_move = target_col.number_input(
        "目标手数（可选）", min_value=1, step=1, value=None,
        key="agent_home_target_move",
    )
    goal = st.text_area(
        "复盘目标", placeholder="例如：帮我找这盘最值得改的三个问题",
        key="agent_home_goal",
    )
    submitted = st.form_submit_button("开始复盘", type="primary", width="stretch")

if submitted:
    st.session_state.pop(SAVE_KEY, None)
    try:
        request = build_review_request(
            uploaded.getvalue() if uploaded is not None else b"",
            user_color,
            goal,
            int(target_move) if target_move is not None else None,
        )
        with st.status("解析棋谱…", expanded=False) as status:
            st.session_state[RESULT_KEY] = run_review_request(
                request,
                progress_callback=lambda message: status.update(label=message),
            )
            status.update(label="分析完成", state="complete")
    except ValueError as error:
        st.error(str(error))
    except Exception as error:
        st.error(f"复盘暂时无法完成：{error}")

run = st.session_state.get(RESULT_KEY)
if run is not None:
    render_result(run)

st.divider()
left, right = st.columns(2)
if left.button("图片复盘", width="stretch"):
    st.session_state["review_input"] = "图片"
    st.switch_page("app_pages/review.py")
if right.button("打开分析工作台", width="stretch"):
    st.session_state["review_input"] = "SGF"
    st.switch_page("app_pages/review.py")
