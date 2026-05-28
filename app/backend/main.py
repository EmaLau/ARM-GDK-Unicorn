# app/backend/main.py
# Hub centrale dell'architettura: espone due endpoint WebSocket.
#   /ui        -- connessione dalla GUI NiceGUI (frontend)
#   /telemetry -- connessione dall'emulatore Unicorn (worker)
# Il backend fa da ponte: i comandi della UI vengono inoltrati all'emulatore
# e la telemetria dell'emulatore viene inoltrata alla UI.

import json
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Aggiunge la root del progetto al path per permettere import assoluti
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from app.backend.core_compiler import ARMCompiler

app = FastAPI(title="ARM GDK Backend Hub")

# Abilita CORS aperto in modo da permettere connessioni locali da qualsiasi origine
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Percorso radice del progetto: due livelli sopra questo file (app/backend -> root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
compiler = ARMCompiler(PROJECT_ROOT)

# Riferimenti globali alle connessioni WebSocket attive.
# Nota: questa implementazione supporta un solo client UI e un solo emulatore
# connessi contemporaneamente.
ui_socket: WebSocket = None
unicorn_socket: WebSocket = None


@app.websocket("/ui")
async def ui_endpoint(websocket: WebSocket):
    """
    Endpoint WebSocket per la GUI NiceGUI.

    Comandi supportati in ingresso (campo 'action' del payload JSON):
      compile          -- compila il codice Assembly fornito nel campo 'code'
      step             -- esegui un singolo passo nell'emulatore
      continue         -- riprendi l'esecuzione continua
      pause            -- metti in pausa l'esecuzione
      restart          -- reimposta l'emulatore allo stato iniziale
      update_breakpoints -- aggiorna la lista dei breakpoint nell'emulatore

    Risponde con messaggi JSON di tipo CONSOLE_LOG per gli esiti della compilazione.
    Per tutti gli altri comandi, il payload viene inoltrato direttamente all'emulatore.
    """
    global ui_socket, unicorn_socket
    await websocket.accept()
    ui_socket = websocket
    print("[UI] NiceGUI connessa correttamente (/ui)")

    try:
        async for message in websocket.iter_text():
            data = json.loads(message)
            action = data.get("action")

            if action == "compile":
                # La compilazione viene gestita interamente dal backend senza coinvolgere l'emulatore
                success = compiler.save_and_compile(data.get("code", ""))
                await websocket.send_json({
                    "type": "CONSOLE_LOG",
                    "message": "[Compiler SUCCESS] Compilazione completata con successo." if success
                               else "[Compiler Errore] Errore di parsing dell'Assembly."
                })
            else:
                # Tutti gli altri comandi vengono inoltrati all'emulatore Unicorn
                if unicorn_socket:
                    try:
                        await unicorn_socket.send_json({
                            "type": "COMMAND",
                            "action": action,
                            "breakpoints": data.get("breakpoints", [])
                        })
                    except Exception:
                        # Se l'invio fallisce l'emulatore si è probabilmente disconnesso
                        unicorn_socket = None
                else:
                    # Nessun emulatore connesso: avvisa la UI senza crashare
                    await websocket.send_json({
                        "type": "CONSOLE_LOG",
                        "message": "Hardware Unicorn disconnesso. Comando ignorato."
                    })
    except WebSocketDisconnect:
        ui_socket = None


@app.websocket("/telemetry")
async def telemetry_endpoint(websocket: WebSocket):
    """
    Endpoint WebSocket per l'emulatore Unicorn Worker.

    Riceve messaggi JSON con lo stato dell'emulatore (PC, registri, memoria,
    output della console, status) e li inoltra alla GUI come UPDATE_STATE.
    Funge da canale di telemetria unidirezionale: emulatore -> GUI.
    """
    global unicorn_socket, ui_socket
    await websocket.accept()
    unicorn_socket = websocket
    print("[Emulatore] Unicorn connesso alla Telemetria (/telemetry)")

    try:
        async for message in websocket.iter_text():
            if ui_socket:
                try:
                    data = json.loads(message)
                    # Normalizza il payload prima di inoltrarlo alla UI
                    await ui_socket.send_json({
                        "type": "UPDATE_STATE",
                        "data": {
                            "pc": data.get("pc", "0x10000"),
                            "registers": data.get("registers", {}),
                            "memory": data.get("memory", {}),
                            "console_output": data.get("console_output", ""),
                            "status": data.get("status", "running")
                        }
                    })
                except Exception:
                    # Se la UI si è disconnessa nel frattempo, azzera il riferimento
                    ui_socket = None
    except WebSocketDisconnect:
        unicorn_socket = None


if __name__ == "__main__":
    import uvicorn
    # Avvio diretto del server uvicorn (normalmente invocato da start.sh / start.bat)
    uvicorn.run("app.backend.main:app", host="127.0.0.1", port=8001, log_level="info")
