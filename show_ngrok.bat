@echo off
title Ngrok Output
echo Showing Ngrok output...
echo Close this window anytime, ngrok will continue running in background.
echo.

:: 显示ngrok输出
ngrok start --config=ngrok.yml --all 