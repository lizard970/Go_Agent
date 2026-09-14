import base64
import hashlib
import copy
import io
import json

import matplotlib.pyplot as plt
import streamlit as st
from dotenv import load_dotenv
from PIL import Image

from analyzer import (
    EMBEDDING_MODEL,
    analyze_high_with_gpt,
    analyze_with_gpt,
    build_embedding_texts,
    get_embeddings,
    get_client,
)
from board_state import stones_to_grid, grid_to_stones
from sgf_ingestion import parse_sgf
from katago_adapter import LocalKataGoAdapter
from dataclasses import asdict

from memory_store import find_most_similar, load_history, save_record


load_dotenv()

st.title("围棋复盘助手")

STATE_CYCLE = ["empty", "black", "white"]
STATE_LABEL = {"empty": "·", "black": "●", "white": "○"}

BOARD_SCHEMA = {
    "name": "go_board",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "board_size": {"type": "integer", "enum": [9, 13, 19]},
            "stones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "color": {"type": "string", "enum": ["black", "white"]},
                    },
                    "required": ["x", "y", "color"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["board_size", "stones"],
        "additionalProperties": False,
    },
}


def draw_board(board_size, stones):
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.set_xlim(-1, board_size)
    ax.set_ylim(-1, board_size)
    ax.set_aspect("equal")
    ax.invert_yaxis()
    for i in range(board_size):
        ax.plot([0, board_size - 1], [i, i], color="black", linewidth=0.5)
        ax.plot([i, i], [0, board_size - 1], color="black", linewidth=0.5)
    for stone in stones:
        face_color = "black" if stone["color"] == "black" else "white"
        ax.add_patch(
            plt.Circle(
                (stone["x"], stone["y"]), 0.45,
                facecolor=face_color, edgecolor="black", linewidth=1, zorder=3,
            )
        )
    ax.axis("off")
    return fig


def validate_board_data(board_data):
    board_size = board_data["board_size"]
    seen = set()
    valid_stones = []
    for stone in board_data["stones"]:
        point = (stone["x"], stone["y"])
        if not (0 <= point[0] < board_size and 0 <= point[1] < board_size):
            continue
        if point in seen:
            continue
        seen.add(point)
        valid_stones.append(stone)
    return {"board_size": board_size, "stones": valid_stones}


def reset_for_new_upload():
    keys = [
        "board_data", "grid", "confirmed", "analysis_result",
        "analysis_context", "memory_saved", "memory_result", "ref_image",
    ]
    for key in keys:
        st.session_state.pop(key, None)
    st.session_state["uploader_version"] = st.session_state.get("uploader_version", 0) + 1


st.subheader("SGF / KataGo 分析")
sgf_upload = st.file_uploader("上传 SGF 棋谱", type=["sgf"], key="sgf_upload")
if sgf_upload is not None:
    try:
        sgf_id = hashlib.sha256(sgf_upload.getvalue()).hexdigest()
        game = parse_sgf(sgf_upload.getvalue())
        st.caption("使用主线（第一个变化）；手数 0 为初始局面。胜率与目差均为黑棋视角。")
        move_number = int(st.number_input("分析手数", min_value=0, max_value=len(game.moves),
                                          value=len(game.moves), key=sgf_id))
        position = game.position(move_number)
        selection = (sgf_id, move_number)
        if st.session_state.get("katago_selection") != selection:
            st.session_state.pop("katago_result", None)
            st.session_state["katago_selection"] = selection
        st.write(game.metadata)
        st.caption(f"规则：{position.rules}；贴目：{position.komi}；下一手：{position.next_player}")
        fig = draw_board(position.board_data["board_size"], position.board_data["stones"])
        st.pyplot(fig)
        plt.close(fig)
        if st.button("分析此局面（KataGo）", key="katago_analyze"):
            with st.spinner("KataGo 分析中..."):
                st.session_state["katago_result"] = LocalKataGoAdapter.from_env().analyze(position)
        result = st.session_state.get("katago_result")
        if result is not None:
            for warning in result.warnings:
                st.warning(warning)
            if result.status != "ok":
                message = "KataGo 不可用，请检查引擎、模型和运行库配置。" if result.status == "unavailable" else "KataGo 分析失败，请检查配置或稍后重试。"
                st.error(f"{message}（{result.status}）")
                st.caption(result.error)
            else:
                st.write(f"黑棋胜率：{result.winrate:.1%}；黑棋领先：{result.score_lead:g} 目；最佳着：{result.best_move}")
                st.write(f"当前手番：{'黑棋' if result.current_player == 'black' else '白棋'}；搜索次数（visits）：{result.visits}")
                st.write("最佳变化（PV）：" + (" → ".join(result.pv) or "无后续变化"))
                st.caption("候选着使用 GTP 坐标（左下角 A1，跳过 I 列）；probability 为策略先验，不是胜率。")
                st.dataframe([{**asdict(candidate), "pv": " → ".join(candidate.pv)}
                              for candidate in result.candidates], hide_index=True)
                with st.expander("完整分析结果"):
                    st.json(asdict(result))
    except ValueError as error:
        st.session_state.pop("katago_result", None)
        st.error(f"SGF / 配置错误：{error}")
else:
    st.session_state.pop("katago_result", None)
    st.session_state.pop("katago_selection", None)


st.subheader("图片 / GPT 复盘")
need_review = st.checkbox("识别完成后我要手动核对/修改棋盘", value=True)
uploader_version = st.session_state.get("uploader_version", 0)
uploaded_file = st.file_uploader(
    "上传棋局截图",
    type=["png", "jpg", "jpeg"],
    key=f"board_uploader_{uploader_version}",
)

if uploaded_file is not None and "board_data" not in st.session_state:
    image = Image.open(uploaded_file)
    st.session_state["ref_image"] = image.copy()
    with st.spinner("正在识别棋盘..."):
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        prompt_text = """识别围棋截图中的棋盘。判断规格（只能是9、13、19路），从左上到右下逐行核对每个交叉点。
坐标规则：左上角交叉点为(0,0)，x向右，y向下。只记录真实存在的黑白棋子，不要把星位、数字、阴影、落子编号或装饰识别为棋子。"""
        try:
            response = get_client().chat.completions.create(
                model="gpt-5.6",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{img_base64}",
                            "detail": "high",
                        }},
                    ],
                }],
                response_format={"type": "json_schema", "json_schema": BOARD_SCHEMA},
            )
            board_data = validate_board_data(json.loads(response.choices[0].message.content))
        except Exception as error:
            st.error(f"棋盘识别失败：{error}")
            st.stop()

        st.session_state["board_data"] = board_data
        if need_review:
            st.session_state["grid"] = stones_to_grid(board_data["board_size"], board_data["stones"])
            st.session_state["confirmed"] = False
        else:
            st.session_state["confirmed"] = True


if "board_data" in st.session_state and need_review and not st.session_state.get("confirmed", False):
    board_size = st.session_state["board_data"]["board_size"]
    grid = st.session_state["grid"]
    if "ref_image" in st.session_state:
        ref_col, _ = st.columns([1, 4])
        with ref_col:
            st.image(st.session_state["ref_image"], caption="原图参考", width=200)

    st.subheader("核对识别结果")
    st.caption("点击格子循环切换：空 → 黑 → 白 → 空；改完后确认")
    for y in range(board_size):
        cols = st.columns(board_size)
        for x in range(board_size):
            with cols[x]:
                if st.button(STATE_LABEL[grid[y][x]], key=f"cell_{x}_{y}"):
                    index = STATE_CYCLE.index(grid[y][x])
                    grid[y][x] = STATE_CYCLE[(index + 1) % len(STATE_CYCLE)]
                    st.rerun()

    if st.button("确认棋盘，进入下一步", type="primary"):
        st.session_state["board_data"] = {
            "board_size": board_size,
            "stones": grid_to_stones(grid, board_size),
        }
        st.session_state["confirmed"] = True
        st.rerun()


if st.session_state.get("confirmed", False):
    board_data = st.session_state["board_data"]
    if "ref_image" in st.session_state:
        ref_col, _ = st.columns([1, 4])
        with ref_col:
            st.image(st.session_state["ref_image"], caption="原图参考", width=200)

    st.subheader("最终棋盘")
    st.pyplot(draw_board(board_data["board_size"], board_data["stones"]))

    user_color_label = st.radio("你执什么颜色", ["黑棋", "白棋"], horizontal=True)
    user_color = "black" if user_color_label == "黑棋" else "white"
    available_factors = ["势力范围/地盘对比", "厚薄判断", "死活状态", "效率评价", "关键漏洞点", "常见术语点评"]
    selected_factors = st.multiselect(
        "选择想听的复盘因素（可多选）",
        available_factors,
        default=["关键漏洞点"],
    )
    tone_choice = st.radio(
        "点评风格", ["温和 🌸", "平实 🧑‍🏫", "毒舌 🌶️"],
        index=1, horizontal=True,
    )

    with st.expander("🛠️ 调试：直接编辑Low版prompt模板（可选）"):
        st.caption("留空则使用analyzer.py默认prompt。占位符：{board_size} {stones_desc} {factors_desc} {tone_desc} {user_color_desc}")
        custom_prompt_override = st.text_area("自定义prompt", height=150, key="custom_prompt")

    if st.button("生成复盘点评", type="primary"):
        with st.spinner("正在生成快速点评..."):
            try:
                override = custom_prompt_override if custom_prompt_override.strip() else None
                result = analyze_with_gpt(board_data, selected_factors, tone_choice, user_color, override)
                st.session_state["analysis_result"] = result
                st.session_state["analysis_context"] = {
                    "board_data": copy.deepcopy(board_data),
                    "factors": list(selected_factors),
                    "tone_choice": tone_choice,
                    "user_color": user_color,
                    "low_commentary": result["commentary"],
                }
                st.session_state["memory_saved"] = False
                st.session_state.pop("memory_result", None)
            except Exception as error:
                st.error(f"点评生成失败：{error}")

    if "analysis_result" in st.session_state:
        st.subheader("复盘点评")
        st.markdown(st.session_state["analysis_result"]["commentary"])

        threshold = st.slider(
            "重复错误提醒阈值", 0.70, 0.95, 0.82, 0.01,
            help="越高越严格。数据较少时先用0.82，积累20～50盘后再校准。",
        )

        if not st.session_state.get("memory_saved", False):
            st.caption("当前只是快速点评，尚未保存，也没有调用embedding。")
            if st.button("加入错题本并分析历史"):
                context = st.session_state["analysis_context"]
                with st.spinner("正在生成详细错题、比较历史并保存..."):
                    try:
                        high_result = analyze_high_with_gpt(
                            context["board_data"], context["factors"],
                            context["tone_choice"], context["user_color"],
                        )
                        game_text, issue_texts = build_embedding_texts(
                            high_result, context["board_data"], context["user_color"]
                        )
                        vectors = get_embeddings([game_text] + issue_texts)
                        game_vector, issue_vectors = vectors[0], vectors[1:]
                        current_issues = [
                            {
                                "issue_type": issue["issue_type"],
                                "embedding": vector,
                            }
                            for issue, vector in zip(high_result["issues"], issue_vectors)
                        ]
                        match = find_most_similar(
                            game_vector, current_issues,
                            context["board_data"]["board_size"],
                        )
                        record_id = save_record(
                            context["board_data"], context["user_color"],
                            context["factors"], context["tone_choice"],
                            context["low_commentary"], high_result,
                            game_text, game_vector, issue_vectors, EMBEDDING_MODEL,
                        )
                        st.session_state["analysis_result"] = {
                            "commentary": high_result["commentary"]
                        }
                        st.session_state["memory_result"] = {
                            "record_id": record_id,
                            "match": match,
                            "threshold": threshold,
                            "count_estimate": high_result["count_estimate"],
                            "issues": high_result["issues"],
                        }
                        st.session_state["memory_saved"] = True
                        st.rerun()
                    except Exception as error:
                        st.error(f"错题本分析或保存失败，本次未写入：{error}")

        if st.session_state.get("memory_saved", False):
            memory_result = st.session_state["memory_result"]
            st.success(f"已保存为错题 #{memory_result['record_id']}；当前共 {len(load_history())} 条记录。")
            with st.expander("查看结构化盘面估算与错误标签"):
                st.json({
                    "count_estimate": memory_result["count_estimate"],
                    "issues": memory_result["issues"],
                })

            match = memory_result["match"]
            if match is None:
                st.info("这是该棋盘规格下的第一条错题，暂无历史记录可比较。")
            else:
                st.subheader("最相似的历史错题")
                st.write(f"记录 #{match['record_id']} · {match['created_at']} · 综合相似度 {match['score']:.3f}")
                if match["issue_pair"]:
                    pair = match["issue_pair"]
                    st.write(f"本次「{pair['current_issue_type']}」最接近历史「{pair['historical_issue_type']}」（{pair['historical_region']}）")
                st.caption(match["position_summary"])
                with st.expander("查看这条历史错题的详细点评"):
                    st.markdown(match["commentary"])
                if match["score"] >= memory_result["threshold"]:
                    st.warning("特别提醒：这与一条历史错题高度相似，可能是你正在重复出现的棋形或判断问题。")

    if st.button("上传新的棋局"):
        reset_for_new_upload()
        st.rerun()
