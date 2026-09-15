import base64
import copy
from dataclasses import asdict
import hashlib
import io
import json

import matplotlib.pyplot as plt
import streamlit as st
from PIL import Image

import analyzer
from board_state import grid_to_stones, stones_to_grid
from game_scan import (GameScanError, metrics_for_color, scan_game,
                       scan_visits_from_env)
from katago_adapter import (KataGoProcessError, LocalKataGoAdapter,
                            PersistentKataGoAdapter)
from memory_store import find_most_similar, load_history, save_record
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


def render_sgf():
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
