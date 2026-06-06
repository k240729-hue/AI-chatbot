@echo off
title Hospital Chatbot - Starting...

echo [1/4] Starting MongoDB...
net start MongoDB >nul 2>&1
if %errorlevel%==0 (echo MongoDB started.) else (echo MongoDB already running.)

echo [2/4] Starting Ollama...
start /B "" "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve >nul 2>&1
timeout /t 3 /nobreak >nul
echo Ollama started.

echo [3/4] Starting Flask app...
start /B "" "C:\Users\talha\Documents\hospital_chatbot\venv\Scripts\python.exe" "C:\Users\talha\Documents\hospital_chatbot\app.py"
timeout /t 3 /nobreak >nul
echo Flask started on http://localhost:5000

echo [4/4] Opening browser...
start http://localhost:5000

echo.
echo ============================================
echo  Hospital Chatbot is running!
echo  URL: http://localhost:5000
echo  MongoDB Compass: localhost:27017
echo  Database: hospital_chatbot
echo ============================================
echo.
pause
