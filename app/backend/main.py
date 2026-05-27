import json
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from app.backend.core_emu import CustomARMDebugger

app = FastAPI(title="Custom ARM Debugger Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SRC_PATH = BASE_DIR / "src" / "main.s"

# Riferimenti globali per i canali di comunicazione
ui_websocket: WebSocket = None
unicorn_websocket: WebSocket = None


@app.get("/")
def read_root():
    return {"status": "Backend running", "target": str(SRC_PATH)}


@app.websocket("/ui")
async def debugger_websocket(websocket: WebSocket):
    global ui_websocket, unicorn_websocket
    await websocket.accept()
    ui_websocket = websocket  # Salva la connessione della UI
    print("🔌 Client UI connesso al WebSocket del Debugger (/ui)")

    debugger = CustomARMDebugger(SRC_PATH)

    try:
        # Stato iniziale generato dal core locale in attesa di Unicorn
        try:
            data_buffer = debugger.pre_process_and_compile()
            debugger.init_vm(data_buffer)
            initial_regs = debugger.get_registers()

            await websocket.send_json({
                "type": "UPDATE_STATE",
                "data": {
                    "pc": initial_regs.get("pc", "0x10000"),
                    "registers": initial_regs,
                    "memory": {},
                    "console_output": "[Backend] Pronto. In attesa di Unicorn...",
                    "status": "running"
                }
            })
        except Exception as e:
            print(f"❌ Errore inizializzazione locale: {e}")

        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            tipo_messaggio = message.get("type")
            comando = message.get("action") if tipo_messaggio == "SEND_COMMAND" else message.get("command")

            if not comando:
                continue

            print(f"📥 UI -> Backend: {comando}")

            # Inoltra il comando a Unicorn
            if unicorn_websocket:
                try:
                    await unicorn_websocket.send_json({
                        "type": "COMMAND",
                        "action": comando,
                        "breakpoints": list(message.get("breakpoints", [])) if "breakpoints" in message else []
                    })
                    print(f"🛰️ Backend -> Unicorn: Inoltrato '{comando}'")
                except Exception as e:
                    print(f"⚠️ Errore inoltro a Unicorn: {e}")
            else:
                print("⚠️ Unicorn non è connesso, il comando non può essere eseguito in hardware")

    except WebSocketDisconnect:
        print("🔌 Client UI disconnesso")
        ui_websocket = None
    except Exception as e:
        print(f"❌ Errore WebSocket UI: {e}")
        ui_websocket = None


@app.websocket("/telemetry")
async def telemetry_websocket(websocket: WebSocket):
    global ui_websocket, unicorn_websocket
    await websocket.accept()
    unicorn_websocket = websocket
    print("🛸 Emulatore Unicorn connesso alla Telemetria (/telemetry)")

    try:
        while True:
            # Riceve i dati in tempo reale da Unicorn (py_qemu_alt.py)
            data = await websocket.receive_text()
            unicorn_data = json.loads(data)

            # Estrae PC e Registri inviati da Unicorn
            pc_attuale = unicorn_data.get("pc", "0x10000")
            registri_attuali = unicorn_data.get("registers", {})
            output_console = unicorn_data.get("console_output", "")

            # --- TOCO DI MAGIA: SPEDISCE I DATI REALI ALLA UI DI NICEGUI ---
            if ui_websocket:
                try:
                    await ui_websocket.send_json({
                        "type": "UPDATE_STATE",
                        "data": {
                            "pc": pc_attuale,
                            "registers": registri_attuali,
                            "memory": unicorn_data.get("memory", {}),
                            "console_output": output_console,
                            "status": "running"
                        }
                    })
                    # Riduciamo il log di stampa per non intasare il terminale
                    print(f"📺 Telemetria -> UI: Aggiornato PC a {pc_attuale}")
                except Exception as e:
                    print(f"⚠️ Impossibile aggiornare la UI: {e}")
            # ---------------------------------------------------------------

    except WebSocketDisconnect:
        print("🛸 Emulatore Unicorn disconnesso")
        unicorn_websocket = None
    except Exception as e:
        print(f"❌ Errore canale telemetria: {e}")
        unicorn_websocket = None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)