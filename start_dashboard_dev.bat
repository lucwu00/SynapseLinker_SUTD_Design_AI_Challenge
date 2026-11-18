@echo off
REM Synapse Linker - React dashboard in development mode on http://localhost:5173 (needs Node.js 18+)
cd /d %~dp0frontend
if not exist node_modules call npm install
npm run dev
