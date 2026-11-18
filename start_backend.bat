@echo off
REM Synapse Linker - start the backend (and the prebuilt dashboard) on http://localhost:8000
cd /d %~dp0
call .venv\Scripts\activate
uvicorn backend.main:app --host 0.0.0.0 --port 8000
