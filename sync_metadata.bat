@echo off
setlocal
"%~dp0.venv\Scripts\python.exe" -u "%~dp0scripts\sync_clip_metadata.py" %*
pause