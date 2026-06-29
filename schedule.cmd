@echo off
python "%~dp0schedule.py" %*
exit /b %ERRORLEVEL%
