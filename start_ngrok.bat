@echo off
title Ngrok Service
echo Starting Ngrok...
echo You can close this window after ngrok starts, it will continue running in background.
echo.

:: 使用 PowerShell 启动后台进程
powershell -Command "Start-Process ngrok -ArgumentList 'start --config=ngrok.yml --all' -WindowStyle Hidden -PassThru"

:: 显示当前运行状态
timeout /t 2 > nul
echo Ngrok is now running in background.
echo You can safely close this window.
echo To stop ngrok, run stop_ngrok.bat
pause 