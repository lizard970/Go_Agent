@echo off
D:
cd Projects\GoGame
call venv\Scripts\activate
streamlit run app.py
pause