import base64
import copy
from dataclasses import asdict
import hashlib
import io
import json

import matplotlib.pyplot as plt
import altair as alt
import streamlit as st
from PIL import Image

import analyzer
from board_state import grid_to_stones, stones_to_grid
from game_scan import (GameScanError, metrics_for_color, scan_game,
                       scan_visits_from_env)
from katago_adapter import (KataGoProcessError, LocalKataGoAdapter,
                            PersistentKataGoAdapter)
from memory_store import find_most_similar, load_history, save_record
from move_commentary import importance_timeline_data
from sgf_ingestion import parse_sgf
from ui import STATE_CYCLE, STATE_LABEL, draw_board


BOARD_SCHEMA = {
    "name": "go_board", "strict": True,
    "schema": {"type": "object", "properties": {
        "board_size": {"type": "integer", "enum": [9, 13, 19]},
        "stones": {"type": "array", "items": {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "color": {"type": "string", "enum": ["black", "white"]}},
            "required": ["x", "y", "color"], "additionalProperties": False}}},
        "required": ["board_size", "stones"], "additionalProperties": False},
}


def validate_board_data(board_data):
    size = board_data["board_size"]
    seen, stones = set(), []
    for stone in board_data["stones"]:
        point = stone["x"], stone["y"]
        if 0 <= point[0] < size and 0 <= point[1] < size and point not in seen:
            seen.add(point)
            stones.append(stone)
    return {"board_size": size, "stones": stones}


def reset_image_review():
    for key in ("board_data", "grid", "confirmed", "analysis_result", "analysis_context",
                "memory_saved", "memory_result", "ref_image"):
        st.session_state.pop(key, None)
    st.session_state["uploader_version"] = st.session_state.get("uploader_version", 0) + 1


def render_katago_result(result, user_color):
    for warning in result.warnings:
        st.warning(warning)
    if result.status != "ok":
        st.error("KataGo 不可用" if result.status == "unavailable" else "KataGo 分析失败")
        st.caption(result.error)
        return
    user_winrate, user_score_lead = metrics_for_color(result.winrate, result.score_lead, user_color)
    color_label = "黑棋" if user_color == "B" else "白棋"
    one, two, three = st.columns(3)
    one.metric("你的胜率", f"{user_winrate:.1%}", border=True)
    two.metric("你的目差", f"{user_score_lead:+.1f}", border=True)
    three.metric("推荐着", result.best_move, border=True)
    st.caption(f"你执：{color_label} · 当前手番：{'黑棋' if result.current_player == 'black' else '白棋'} "
               f"· visits：{result.visits} · 原始证据为黑棋视角")
    st.markdown("**最佳变化**　" + (" → ".join(result.pv) or "无后续变化"))
    with st.expander("候选着与完整证据"):
        st.dataframe([{**asdict(candidate), "pv": " → ".join(candidate.pv)}
                      for candidate in result.candidates], hide_index=True)
        st.json(asdict(result))


def render_agent_player(run):
    game = run.game
    state = run.result.state
    scan = state.scan_result
    if scan is None or not state.move_importance:
        st.info("当前 Agent 结果不是整盘复盘，尚无可播放的全盘缓存。")
        return
    key = f"workspace_agent_move_{state.game_id}"
    st.session_state.setdefault(key, 1)
    st.session_state[key] = max(1, min(len(game.moves), st.session_state[key]))
    current = st.session_state[key]

    move = scan.moves[current - 1]
    importance = state.move_importance[current - 1]
    rows = importance_timeline_data(state.move_importance, current)
    base = alt.Chart(alt.Data(values=rows)).encode(
        x=alt.X("move_number:Q", title="手数"),
        y=alt.Y("importance_score:Q", title="重要度", scale=alt.Scale(domain=[0, 1])),
    )
    line = base.mark_area(opacity=.22, color="#8f4b58", line=True)
    points = base.mark_circle(size=38).encode(
        color=alt.Color(
            "polarity:N", title="关注方向",
            scale=alt.Scale(domain=["negative", "positive", "neutral"],
                            range=["#9d4055", "#5f7d5f", "#9a8d84"]),
        ),
        tooltip=["move_number:Q", "importance_score:Q", "commentary_level:N"],
    )
    cursor = alt.Chart(alt.Data(values=[{"move_number": current}])).mark_rule(
        color="#33282d", strokeWidth=2
    ).encode(x="move_number:Q")
    st.markdown("""<style>
    .st-key-workspace_timeline {opacity:.48; transition:opacity .18s ease;}
    .st-key-workspace_timeline:hover {opacity:1;}
    </style>""", unsafe_allow_html=True)

    def step_move(delta):
        st.session_state[key] = max(
            1, min(len(game.moves), st.session_state[key] + delta)
        )

    board_col, detail_col = st.columns([1.18, .82], gap="medium")
    with board_col:
        with st.container(border=True):
            st.subheader(f"第 {current} 手后的局面")
            fig = draw_board(game.size, game.position(current).board_data["stones"])
            st.pyplot(fig, width="stretch")
            plt.close(fig)
        with st.container(key="workspace_timeline"):
            st.altair_chart(line + points + cursor, width="stretch")
    with detail_col:
        with st.container(border=True):
            marker = " · Top 错误" if importance.is_top_error else ""
            st.subheader(f"第 {current} 手 · {'黑棋' if move.player == 'B' else '白棋'}{marker}")
            user_before = metrics_for_color(move.black_winrate_before, move.black_score_lead_before, move.player)
            user_after = metrics_for_color(move.black_winrate_after, move.black_score_lead_after, move.player)
            one, two, three = st.columns(3)
            one.metric("实战", move.actual_move)
            two.metric("推荐", move.best_move_before)
            three.metric("重要度", f"{importance.importance_score:.0%}")
            st.caption(
                f"行棋方胜率 {user_before[0]:.1%} → {user_after[0]:.1%} · "
                f"目差 {user_before[1]:+.1f} → {user_after[1]:+.1f} · {move.phase}"
            )
            comment = state.move_comments.get(current)
            if comment is not None and comment.status == "succeeded":
                st.markdown(f"**{comment.headline}**\n\n{comment.commentary}")
            elif importance.commentary_level == "quiet":
                st.caption("这一手没有发现值得单独展开的问题。")
            else:
                st.warning("这一手值得关注，但批量点评暂不可用；KataGo 证据已保留。")

            for signal in state.history_summary.get("repeated_error_candidates", []):
                index = signal.get("evidence_index")
                if index is not None and index < len(state.deep_evidence) and state.deep_evidence[index].worst_move_number == current:
                    st.info(
                        f"找到 {signal['candidate_count']} 条潜在相似历史记录；"
                        "相似度不代表已经确认是同一种错误。"
                    )
        st.container(height=56, border=False)
        with st.container(border=True, key="workspace_player_controls"):
            st.caption(f"复盘进度 · 第 {current} / {len(game.moves)} 手")
            previous, slider_col, next_col = st.columns([.24, .52, .24], vertical_alignment="bottom")
            previous.button(
                "上一手", key="workspace_previous", disabled=current <= 1,
                width="stretch", on_click=step_move, args=(-1,),
            )
            slider_col.slider(
                "手数", 1, len(game.moves), key=key,
                label_visibility="collapsed",
            )
            next_col.button(
                "下一手", key="workspace_next", disabled=current >= len(game.moves),
                width="stretch", on_click=step_move, args=(1,),
            )


def render_saved_mistake(context):
    source = context["source_content"]
    game = parse_sgf(source if isinstance(source, bytes) else source.encode("utf-8"))
    move_number = context["worst_move_number"]
    snapshot = context.get("board_snapshot")
    board_data = None
    if snapshot is not None:
        board_data = {
            "board_size": snapshot["board_size"],
            "stones": [
                {"x": point[0], "y": point[1], "color": color}
                for color in ("black", "white")
                for point in snapshot.get(color, [])
            ],
        }
    if board_data is None:
        board_data = game.position(move_number).board_data
    board, detail = st.columns([1.18, .82], gap="medium")
    with board.container(border=True):
        st.subheader(f"第 {move_number} 手后的历史局面")
        fig = draw_board(game.size, board_data["stones"])
        st.pyplot(fig, width="stretch")
        plt.close(fig)
    with detail.container(border=True):
        st.subheader(context.get("title") or f"第 {move_number} 手")
        st.caption(
            f"实战 {context['actual_move']} · 推荐 {context['recommended_move']} · "
            f"胜率损失 {context['player_winrate_loss']:.1%} · "
            f"目差损失 {context['player_score_loss']:.1f}"
        )
        st.write(context.get("summary") or "解释尚未完成。")
        if context.get("why_it_matters"):
            st.write(f"为什么重要：{context['why_it_matters']}")
        if context.get("better_plan"):
            st.write(f"更好的思路：{context['better_plan']}")
        if context.get("learning_point"):
            st.write(f"复用要点：{context['learning_point']}")
    st.caption("此历史局面从本地 SGF 与保存快照恢复，未调用 KataGo 或 OpenAI。")


def render_sgf():
    cached = st.session_state.get("agent_home_review_run")
    mistake_context = st.session_state.get("workspace_mistake_context")
    ignore_cached = st.session_state.get("workspace_ignore_agent_cache", False)
    has_full_player_cache = (
        cached is not None
        and cached.result.state.scan_result is not None
        and cached.result.state.move_importance
    )
    if has_full_player_cache and not ignore_cached:
        st.subheader("整盘逐手复盘")
        render_agent_player(cached)
        if st.button("切换到手动分析", key="workspace_manual_mode"):
            st.session_state["workspace_ignore_agent_cache"] = True
            st.rerun()
        return
    if mistake_context is not None and not has_full_player_cache:
        st.subheader("历史错题局面")
        render_saved_mistake(mistake_context)
        if st.button("切换到手动分析", key="workspace_saved_manual_mode"):
            st.session_state.pop("workspace_mistake_context", None)
            st.rerun()
        return
    if has_full_player_cache and ignore_cached:
        if st.button("返回 Agent 逐手复盘", key="workspace_agent_mode"):
            st.session_state["workspace_ignore_agent_cache"] = False
            st.rerun()
    upload_col, move_col, color_col = st.columns([1.15, .75, .55], gap="medium", vertical_alignment="bottom")
    with upload_col:
        uploaded = st.file_uploader("上传 SGF 棋谱", type=["sgf"], key="sgf_upload")
    with color_col:
        user_color = st.segmented_control("你执", ["B", "W"], default="B", required=True,
                                          format_func=lambda color: "黑棋" if color == "B" else "白棋",
                                          key="sgf_user_color", persist_state="session")
    if uploaded is None:
        st.session_state.pop("katago_result", None)
        st.session_state.pop("katago_selection", None)
        st.session_state.pop("game_scan_result", None)
        st.session_state.pop("game_scan_selection", None)
        st.info("上传 SGF 后可选择任意局面。KataGo 只在点击分析时运行。")
        return
    try:
        raw = uploaded.getvalue()
        sgf_id = hashlib.sha256(raw).hexdigest()
        game = parse_sgf(raw)
        with move_col:
            move_number = st.slider("局面手数", 0, len(game.moves), len(game.moves), key=f"sgf_move_{sgf_id}")
        position = game.position(move_number)
    except ValueError as error:
        st.error(f"SGF 无法读取：{error}")
        return

    selection = sgf_id, move_number, st.session_state["review_depth"]
    if st.session_state.get("katago_selection") != selection:
        st.session_state.pop("katago_result", None)
        st.session_state["katago_selection"] = selection

    board_col, evidence_col = st.columns([.85, 1.15], gap="medium")
    with board_col.container(border=True, key="board_panel"):
        st.subheader("局面")
        meta = game.metadata
        st.caption(f"{uploaded.name} · 第 {move_number}/{len(game.moves)} 手 · "
                   f"{meta.get('PB', '黑棋')} vs {meta.get('PW', '白棋')} · "
                   f"{position.rules} · 贴目 {position.komi:g}")
        fig = draw_board(position.board_data["board_size"], position.board_data["stones"])
        st.pyplot(fig, width="stretch")
        plt.close(fig)
    with evidence_col:
        with st.container(border=True, key="evidence_panel"):
            top = st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center")
            top.subheader("KataGo 分析")
            visits = 150 if st.session_state["review_depth"] == "快速" else 500
            if top.button(f"开始分析 · {visits} visits", type="primary", key="katago_analyze"):
                with st.spinner("KataGo 正在计算当前局面…"):
                    st.session_state["katago_result"] = LocalKataGoAdapter.from_env().analyze(
                        position, max_visits=visits)
            result = st.session_state.get("katago_result")
            if result is None:
                st.caption("尚未分析。")
            else:
                render_katago_result(result, user_color)
        with st.container(border=True, key="explanation_panel"):
            st.subheader("LLM 解释")
            st.caption("当前阶段尚未接入 KataGo Evidence → LLM；这里不会展示原型中的虚构解释。")
            st.button("加入错题本", disabled=True, help="需先完成后续 Evidence → LLM 与事件级存储。")

    try:
        scan_visits = scan_visits_from_env()
    except ValueError as error:
        st.error(str(error))
        scan_visits = None
    scan_selection = sgf_id, scan_visits
    if st.session_state.get("game_scan_selection") != scan_selection:
        st.session_state.pop("game_scan_result", None)
        st.session_state["game_scan_selection"] = scan_selection
    st.subheader("全盘扫描")
    st.caption(f"固定分析初始局面及每手后的局面，共 {len(game.moves) + 1} 次；不执行关键手排序。")
    scan_label = f"扫描全局 · {scan_visits} visits" if scan_visits is not None else "扫描全局 · 配置错误"
    if st.button(scan_label, key="scan_game", disabled=scan_visits is None):
        st.session_state.pop("game_scan_result", None)
        adapter = PersistentKataGoAdapter.from_env()
        try:
            with st.spinner(f"正在顺序分析 {len(game.moves) + 1} 个局面…"):
                st.session_state["game_scan_result"] = scan_game(game, adapter, max_visits=scan_visits)
        except (GameScanError, KataGoProcessError, ValueError) as error:
            st.error(f"全盘扫描失败：{error}")
        finally:
            adapter.close()
    scan_result = st.session_state.get("game_scan_result")
    if scan_result is not None:
        st.success(f"已完成 {len(scan_result.position_analyses)} 个局面、{len(scan_result.moves)} 手。")
        rows = []
        for move in scan_result.moves:
            user_before = metrics_for_color(move.black_winrate_before, move.black_score_lead_before, user_color)
            user_after = metrics_for_color(move.black_winrate_after, move.black_score_lead_after, user_color)
            rows.append({"手数": move.move_number, "行棋方": "黑" if move.player == "B" else "白",
                         "本人": move.player == user_color, "实战": move.actual_move,
                         "推荐": move.best_move_before, "你的胜率·前": user_before[0],
                         "你的胜率·后": user_after[0], "你的目差·前": user_before[1],
                         "你的目差·后": user_after[1], "行棋方胜率损失": move.player_winrate_loss,
                         "行棋方目差损失": move.player_score_loss, "visits": move.visits,
                         "warnings": " · ".join(move.warnings)})
        st.dataframe(rows, hide_index=True, width="stretch")


def recognize_image(uploaded):
    image = Image.open(uploaded)
    st.session_state["ref_image"] = image.copy()
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    prompt = """识别围棋截图中的棋盘。判断规格（只能是9、13、19路），从左上到右下逐行核对每个交叉点。
坐标规则：左上角交叉点为(0,0)，x向右，y向下。只记录真实存在的黑白棋子，不要把星位、数字、阴影、落子编号或装饰识别为棋子。"""
    response = analyzer.get_client().chat.completions.create(
        model="gpt-5.6", messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode(), "detail": "high"}}]}],
        response_format={"type": "json_schema", "json_schema": BOARD_SCHEMA})
    return validate_board_data(json.loads(response.choices[0].message.content))


def save_deep_review():
    context = st.session_state["analysis_context"]
    try:
        with st.spinner("正在生成深度错题、比较历史并保存…"):
            high = analyzer.analyze_high_with_gpt(context["board_data"], context["factors"],
                                                  context["tone_choice"], context["user_color"])
            game_text, issue_texts = analyzer.build_embedding_texts(high, context["board_data"], context["user_color"])
            vectors = analyzer.get_embeddings([game_text] + issue_texts)
            match = find_most_similar(vectors[0], [{"issue_type": issue["issue_type"], "embedding": vector}
                                      for issue, vector in zip(high["issues"], vectors[1:])], context["board_data"]["board_size"])
            record_id = save_record(context["board_data"], context["user_color"], context["factors"],
                context["tone_choice"], context["low_commentary"], high, game_text, vectors[0], vectors[1:], analyzer.EMBEDDING_MODEL)
        st.session_state["analysis_result"] = {"commentary": high["commentary"]}
        st.session_state["memory_result"] = {"record_id": record_id, "match": match, "threshold": .82}
        st.session_state["memory_saved"] = True
        st.rerun()
    except Exception as error:
        st.error(f"深度分析或保存失败，本次未写入：{error}")


def render_image():
    need_review = st.checkbox("识别后手动核对棋盘", value=True, key="need_board_review")
    version = st.session_state.get("uploader_version", 0)
    uploaded = st.file_uploader("上传棋局截图", type=["png", "jpg", "jpeg"], key=f"board_uploader_{version}")
    if uploaded is not None and "board_data" not in st.session_state:
        try:
            with st.spinner("正在识别棋盘…"):
                board_data = recognize_image(uploaded)
            st.session_state["board_data"] = board_data
            st.session_state["confirmed"] = not need_review
            if need_review:
                st.session_state["grid"] = stones_to_grid(board_data["board_size"], board_data["stones"])
        except Exception as error:
            st.error(f"棋盘识别失败：{error}")
            return
    if "board_data" not in st.session_state:
        st.info("图片识别使用现有 GPT 多模态接口；不会调用 KataGo。")
        return
    if need_review and not st.session_state.get("confirmed", False):
        size, grid = st.session_state["board_data"]["board_size"], st.session_state["grid"]
        st.subheader("核对识别结果")
        st.caption("点击交叉点循环切换：空 → 黑 → 白 → 空。")
        for y in range(size):
            cols = st.columns(size, gap="small")
            for x in range(size):
                if cols[x].button(STATE_LABEL[grid[y][x]], key=f"cell_{x}_{y}", width="stretch"):
                    index = STATE_CYCLE.index(grid[y][x])
                    grid[y][x] = STATE_CYCLE[(index + 1) % len(STATE_CYCLE)]
                    st.rerun()
        if st.button("确认棋盘", type="primary"):
            st.session_state["board_data"] = {"board_size": size, "stones": grid_to_stones(grid, size)}
            st.session_state["confirmed"] = True
            st.rerun()
        return
    board_data = st.session_state["board_data"]
    board_col, controls = st.columns([.9, 1.1], gap="medium")
    with board_col.container(border=True):
        st.subheader("确认局面")
        fig = draw_board(board_data["board_size"], board_data["stones"])
        st.pyplot(fig, width="stretch")
        plt.close(fig)
    with controls.container(border=True):
        user_color_label = st.segmented_control("你执哪一方", ["黑棋", "白棋"], default="黑棋",
                                                required=True, key="user_color")
        user_color = "black" if user_color_label == "黑棋" else "white"
        factors = st.multiselect("复盘重点", ["势力范围/地盘对比", "厚薄判断", "死活状态", "效率评价", "关键漏洞点", "常见术语点评"],
                                 default=["关键漏洞点"])
        tone = st.segmented_control("点评风格", ["温和 🌸", "平实 🧑‍🏫", "毒舌 🌶️"], default="平实 🧑‍🏫",
                                    required=True, key="tone")
        if st.button("生成快速点评", type="primary", width="stretch"):
            try:
                with st.spinner("正在生成快速点评…"):
                    result = analyzer.analyze_with_gpt(board_data, factors, tone, user_color)
                st.session_state["analysis_result"] = result
                st.session_state["analysis_context"] = {"board_data": copy.deepcopy(board_data), "factors": list(factors),
                    "tone_choice": tone, "user_color": user_color, "low_commentary": result["commentary"]}
                st.session_state["memory_saved"] = False
                st.session_state.pop("memory_result", None)
            except Exception as error:
                st.error(f"点评生成失败：{error}")
        if "analysis_result" in st.session_state:
            st.markdown(st.session_state["analysis_result"]["commentary"])
            if not st.session_state.get("memory_saved", False):
                if st.button("深度分析并加入错题本", width="stretch"):
                    save_deep_review()
            else:
                saved = st.session_state["memory_result"]
                st.success(f"已保存为错题 #{saved['record_id']}，错题本共 {len(load_history())} 条。")
                match = saved["match"]
                if match and match["score"] >= saved["threshold"]:
                    st.warning(f"与历史错题 #{match['record_id']} 高度相似（{match['score']:.3f}）。")
        if st.button("上传新图片"):
            reset_image_review()
            st.rerun()


with st.container(key="review_header"):
    st.header("新建复盘")
st.session_state.setdefault("review_input", "SGF")
top_left, top_right = st.columns(2)
input_kind = top_left.segmented_control("输入方式", ["图片", "SGF"], default=st.session_state["review_input"],
                                        required=True, key="review_input_control")
st.session_state["review_input"] = input_kind
top_right.segmented_control("分析模式", ["快速", "深度"], default="快速",
                            required=True, key="review_depth", persist_state="session")
if input_kind == "SGF":
    render_sgf()
else:
    render_image()
