@echo off
REM Synapse Linker - DEMO DAY with the real EDi-01 (OjanBot ESP32) on the 60 x 60 cm table.
REM 1. Turn on Windows Mobile Hotspot first (Settings > Network > Mobile hotspot), 2.4 GHz band.
REM 2. Put EDi-01 on the START mark facing right (counter-clockwise), then run this file.
REM 3. Phone on the robot:  http://192.168.137.1:8000/#/robot/EDi-01
REM    Virtual robot laptop: http://192.168.137.1:8000/#/robot/EDi-02
REM    Wall display: http://localhost:8000/#/      Presenter: http://localhost:8000/#/presenter
cd /d %~dp0
call .venv\Scripts\activate
set SYNAPSE_ROBOT_ADAPTER=esp32
set SYNAPSE_MAP=config/table_map_60cm.json
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo   This laptop's address:%%a
uvicorn backend.main:app --host 0.0.0.0 --port 8000
