@echo off
cd /d "%~dp0"
set "PROJECT_DIR=%cd%"

echo [START] Configurazione ed esecuzione dell'architettura modulare...

if not exist "env" (
    echo [INIT] Creazione del virtual environment con Python 3.12...
    python -m venv env
)

echo [SETUP] Controllo e installazione dei requisiti...
call env\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
call env\Scripts\deactivate.bat

echo [INFO] Ambiente pronto. Lancio i moduli in finestre separate...
echo ------------------------------------------------------------------

echo [INFO] Avvio Backend FastAPI...
start "Backend FastAPI" cmd /k "cd /d "%PROJECT_DIR%" && call env\Scripts\activate.bat && set PYTHONPATH=. && python app/backend/main.py"

timeout /t 2 /nobreak >nul

echo [INFO] Avvio Interfaccia Grafica NiceGUI...
start "Frontend NiceGUI" cmd /k "cd /d "%PROJECT_DIR%" && call env\Scripts\activate.bat && python app/frontend/run.py"

timeout /t 1 /nobreak >nul

echo [INFO] Avvio Emulatore Unicorn Worker...
start "Unicorn Worker" cmd /k "cd /d "%PROJECT_DIR%" && call env\Scripts\activate.bat && python app/emulator/unicorn_worker.py"

echo [SUCCESS] Tutti e 3 i moduli sono stati avviati correttamente nelle loro finestre.
pause
