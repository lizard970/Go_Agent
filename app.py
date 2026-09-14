import streamlit as st

from ui import apply_shell


st.set_page_config(page_title="弈析", page_icon="⚫", layout="wide")
apply_shell()

pages = [
    st.Page("app_pages/home.py", title="首页", icon=":material/home:", default=True),
    st.Page("app_pages/review.py", title="新建复盘 / SGF复盘", icon=":material/add_circle:"),
    st.Page("app_pages/mistakes.py", title="错题本", icon=":material/library_books:"),
    st.Page("app_pages/dashboard.py", title="成长看板", icon=":material/monitoring:"),
]
st.navigation(pages, position="sidebar").run()
