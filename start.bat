@echo off
rem EU4-like 多国 AI 对战 · 一键启动（双击入口，转发给 start.ps1）
rem 带参数如：start.bat --new --turns 10
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
