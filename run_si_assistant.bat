@echo off
rem Double-click to start Chem SI Assistant (uses the "si-assistant" Anaconda environment).
cd /d "%~dp0"
"%USERPROFILE%\anaconda3\envs\si-assistant\pythonw.exe" -m si_assistant %*
