import matplotlib.pyplot as plt
import streamlit as st


STATE_CYCLE = ["empty", "black", "white"]
STATE_LABEL = {"empty": "·", "black": "●", "white": "○"}


def apply_shell():
    st.html("""
    <style>
    [data-testid="stAppViewContainer"] { background: radial-gradient(circle at 86% 3%, #e9dfe522, transparent 28%); }
    [data-testid="stSidebar"] { border-right: 1px solid #dfd4da; }
    [data-testid="stSidebarNav"]::before { content: "弈析"; display: block; font-family: Georgia, "Songti SC", serif;
      font-size: 1.8rem; font-weight: 650; letter-spacing: .12em; padding: .55rem 1rem 1.2rem; }
    [data-testid="stMainBlockContainer"] { padding-top: .8rem; padding-bottom: 2rem; max-width: 1480px; }
    .st-key-page_banner { background: linear-gradient(135deg,#fbf8fa,#eee6eb); border: 1px solid #e3d9df;
      border-radius: 1.2rem; padding: .35rem 1.05rem; margin-bottom: .45rem; }
    .st-key-page_banner > div { gap: .15rem; }
    .st-key-page_banner h1 { font-family: Georgia, "Songti SC", serif; font-size: 1.75rem;
      letter-spacing: .04em; margin: 0; }
    .st-key-page_banner [data-testid="stCaptionContainer"] { margin-top: -.25rem; }
    .st-key-review_header { padding-top: 3rem; }
    .st-key-review_header h2 { margin: 0; }
    .st-key-board_panel, .st-key-evidence_panel, .st-key-explanation_panel { transition: opacity .18s ease, box-shadow .18s ease; }
    div[class*="st-key-mistake_dim_"] { opacity: .42; filter: saturate(.65); transition: opacity .2s ease; }
    div[class*="st-key-mistake_selected_"] { opacity: 1; box-shadow: 0 10px 28px #4b333f18;
      border-color: #947184 !important; transition: box-shadow .2s ease; }
    @media (max-width: 900px) { [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; } }
    </style>""")


def page_banner(title, caption):
    with st.container(key="page_banner"):
        st.title(title)
        st.caption(caption)


def draw_board(board_size, stones, size=4.6):
    fig, ax = plt.subplots(figsize=(size, size))
    fig.patch.set_facecolor("#ead0a0")
    ax.set_facecolor("#ead0a0")
    ax.set_xlim(-0.6, board_size - 0.4)
    ax.set_ylim(-0.6, board_size - 0.4)
    ax.set_aspect("equal")
    ax.invert_yaxis()
    for i in range(board_size):
        ax.plot([0, board_size - 1], [i, i], color="#5f4631", linewidth=0.65)
        ax.plot([i, i], [0, board_size - 1], color="#5f4631", linewidth=0.65)
    for stone in stones:
        face = "#272329" if stone["color"] == "black" else "#faf8f5"
        ax.add_patch(plt.Circle((stone["x"], stone["y"]), 0.43, facecolor=face,
                                edgecolor="#302b2e", linewidth=0.8, zorder=3))
    ax.axis("off")
    fig.tight_layout(pad=.2)
    return fig
