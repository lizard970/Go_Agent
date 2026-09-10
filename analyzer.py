import json
import os

import httpx
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    http_client=httpx.Client(proxy="http://127.0.0.1:9567"),
)

GPT_MODEL = "gpt-5.6"
EMBEDDING_MODEL = "text-embedding-3-small"


def tone_description(tone_choice):
    """把风格选择转换成语气描述。"""
    if tone_choice == "温和 🌸":
        return "像一位耐心的启蒙教练。先肯定真实优点，再用商量、引导式语言指出问题；温暖但不回避结论。"
    if tone_choice == "平实 🧑‍🏫":
        return "像一位大学讲师评讲作业。用词精准、专业，直接陈述事实，语言干净利落。"
    return "非常毒舌的解说，可以利用中文互联网的潮流的梗，用词夸张、生活化、可以最刻薄地调侃棋形，但所有吐槽必须有盘面依据；可以调侃用户的智力、人格或学习能力。若用户下得不错，可以傲娇地提及。"


def _board_context(board_data, factors, tone_choice, user_color):
    stones_desc = "\n".join(
        f"({s['x']}, {s['y']}) - {'黑子' if s['color'] == 'black' else '白子'}"
        for s in board_data["stones"]
    )
    return {
        "board_size": board_data["board_size"],
        "stones_desc": stones_desc,
        "factors_desc": "、".join(factors) if factors else "整体局面",
        "tone_desc": tone_description(tone_choice),
        "user_color_desc": "黑棋" if user_color == "black" else "白棋",
    }


def _static_position_rules():
    return """证据和计目规则：
1. 这是静态快照。你不知道当前轮到谁、此前落子顺序、历史提子数；不得虚构“这手导致”“你之前连续”等过程。
2. 按日式规则理解“目”：已被一方活棋可靠围住的空点可计作当前实地；棋子本身不算目。未封闭、可侵入、死活未定或双方接触的空点属于潜在地域/未定区域，不能硬算成实地。
3. 必须估算当前盘面的实地：分别给出黑、白已较可靠实地的合理范围，并指出双方潜在地域与最大未定区域。允许使用区间，但不允许仅以“贴目或提子数不明”为由拒绝估算。
4. 贴目和历史提子数只影响最终总目差。若它们未知，应明确写“以下为不含贴目和历史提子的盘面实地粗估”，仍要给出范围和当前盘面领先方向。
5. 不知道当前手番时，对依赖先手的结论分别说明“若黑先/若白先”；“黑棋在开局先行”不等于当前轮到黑棋。
6. 没有 KataGo 计算，不得编造精确胜率、唯一最佳着或确定的最终目差。建议只能表述为候选方向。
7. 死活只能根据可见棋形判断为已活、已死、未安定或依赖手番；真眼、假眼、气、连接和断点必须对应具体坐标。"""


def analyze_with_gpt(board_data, factors, tone_choice, user_color, custom_prompt=None):
    """生成第一次展示的 Low 版静态点评。"""
    context = _board_context(board_data, factors, tone_choice, user_color)

    if custom_prompt:
        analysis_prompt = custom_prompt.format(**context)
    else:
        analysis_prompt = f"""角色：你是一名严谨、善于给初学者讲解的围棋老师。

目标：分析一盘{context['board_size']}路围棋的当前静态局面。坐标以左上角为(0,0)，x向右、y向下。

棋子分布：
{context['stones_desc']}

用户执{context['user_color_desc']}，重点点评：{context['factors_desc']}。

{_static_position_rules()}

成功标准：
- 先客观判断用户方大致有利、不利或接近，不预设用户下得差。
- 必须包含“盘面估算”：黑白当前可靠实地各约多少目（可给区间）、潜在地域、主要未定区域，以及不含贴目和历史提子的盘面领先方向。
- 每个重要判断都对应具体坐标、区域或棋块，并说明威胁与改进方向。
- 用户方局面不错就如实肯定；只批评确有依据的问题。

表达风格：{context['tone_desc']}

按“形势与盘面估算—具体证据—关键问题—改进建议”组织；毒舌风格可以自然连贯，但不能牺牲技术准确性。直接输出点评，不要开场白，控制在450字以内。"""

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": analysis_prompt}],
    )
    return {"commentary": response.choices[0].message.content}


HIGH_ANALYSIS_SCHEMA = {
    "name": "go_review_memory",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "commentary": {"type": "string"},
            "position_summary": {"type": "string"},
            "count_estimate": {
                "type": "object",
                "properties": {
                    "black_secure_min": {"type": "integer"},
                    "black_secure_max": {"type": "integer"},
                    "white_secure_min": {"type": "integer"},
                    "white_secure_max": {"type": "integer"},
                    "black_potential": {"type": "string"},
                    "white_potential": {"type": "string"},
                    "unsettled_regions": {"type": "array", "items": {"type": "string"}},
                    "board_lead_estimate": {"type": "string"},
                    "assumptions": {"type": "string"},
                },
                "required": ["black_secure_min", "black_secure_max", "white_secure_min", "white_secure_max", "black_potential", "white_potential", "unsettled_regions", "board_lead_estimate", "assumptions"],
                "additionalProperties": False,
            },
            "issues": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "issue_type": {"type": "string"},
                        "region": {"type": "string"},
                        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                        "evidence": {"type": "string"},
                        "improvement": {"type": "string"},
                        "embedding_text": {"type": "string"},
                    },
                    "required": ["issue_type", "region", "severity", "evidence", "improvement", "embedding_text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["commentary", "position_summary", "count_estimate", "issues"],
        "additionalProperties": False,
    },
}


def analyze_high_with_gpt(board_data, factors, tone_choice, user_color):
    """用户确认加入错题本后，生成 High 点评及可检索的结构化错误。"""
    context = _board_context(board_data, factors, tone_choice, user_color)
    prompt = f"""角色：你是一名严谨的围棋复盘老师兼错题标注员。

目标：对下面的{context['board_size']}路静态局面生成更完整的复盘，并提取最多3个真正重要、彼此可区分的错误模式，供以后语义检索。

棋子分布：
{context['stones_desc']}

用户执{context['user_color_desc']}，关注因素：{context['factors_desc']}。

{_static_position_rules()}

输出内容要求：
- commentary：450字以内，包含形势、黑白可靠实地区间、潜在地域/未定区域、盘面领先方向、具体证据和改进建议。语气为：{context['tone_desc']}
- position_summary：100字以内的中性局面摘要；禁止毒舌、情绪词和空泛评价，用“棋盘规格、用户方、阶段、势力/实地、棋块安全、主要问题”描述，供局面级 embedding。
- count_estimate：必须填写黑白可靠实地区间；它是不含贴目和历史提子的当前盘面粗估，不是精确终局结果。
- issues：只记录用户方确有依据的主要问题，最多3个；若用户方没有明显错误，可以为空数组，不能凑数。
- issue_type 使用稳定、可复用的围棋概念，例如：忽视断点、死活误判、棋形低效、方向错误、过度集中、脱先过早、收官次序、厚薄判断错误。
- evidence 必须引用可见区域、棋块或坐标，不得虚构落子过程。
- embedding_text 必须是独立可理解的中性错题描述，包含“用户方、错误类型、局部棋形/区域、可见证据、改进原则”；禁止毒舌措辞和本盘无关内容。

只输出符合 schema 的结果。"""

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema": HIGH_ANALYSIS_SCHEMA},
    )
    return json.loads(response.choices[0].message.content)


def build_embedding_texts(high_result, board_data, user_color):
    """构造一个局面级文本和最多三个错误级文本，避免语气污染向量。"""
    user_color_desc = "黑棋" if user_color == "black" else "白棋"
    issue_types = "、".join(issue["issue_type"] for issue in high_result["issues"]) or "无明显错误"
    game_text = f"局面级错题；{board_data['board_size']}路；用户执{user_color_desc}；{high_result['position_summary']}；主要问题：{issue_types}"
    return game_text, [issue["embedding_text"] for issue in high_result["issues"]]


def get_embeddings(texts):
    """批量生成 embedding；输入顺序与输出顺序一致。"""
    if not texts:
        return []
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def analyze_with_katago(board_data, factors, tone_choice, user_color):
    """KataGo预留接口。"""
    raise NotImplementedError("KataGo分析功能尚未实现")
