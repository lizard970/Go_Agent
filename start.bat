@echo off
setlocal
cd /d "%~dp0"
rem Only this launcher and its children inherit the cuDNN path.
if not defined KATAGO_DLL_DIRECTORY set "KATAGO_DLL_DIRECTORY=D:\NVIDIA\cuDNN\9.8.0.87\cudnn-windows-x86_64-9.8.0.87_cuda12-archive\bin"
set "PATH=%KATAGO_DLL_DIRECTORY%;%PATH%"
"%~dp0venv\Scripts\python.exe" -m streamlit run "%~dp0app.py" %*
pause
endlocal
