# app/backend/main.py
import json
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from app.backend.core_compiler import ARMCompiler

app = FastAPI(title="ARM GDK Backend Hub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
compiler = ARMCompiler(PROJECT_ROOT)

ui_socket: WebSocket = None
unicorn_socket: WebSocket = None


@app.websocket("/ui")
async def ui_endpoint(websocket: WebSocket):
    global ui_socket, unicorn_socket
    await websocket.accept()
    ui_socket = websocket
    print("🔌 UI NiceGUI connessa correttamente (/ui)")

    try:
        async for message in websocket.iter_text():
            data = json.loads(message)
            action = data.get("action")

            if action == "compile":
                success = compiler.save_and_compile(data.get("code", ""))
                await websocket.send_json({
                    "type": "CONSOLE_LOG",
                    "message": "[Compiler SUCCESS] Compilazione completata con successo." if success else "[Compiler Errore] Errore di parsing dell'Assembly."
                })
            else:
                if unicorn_socket:
                    try:
                        await unicorn_socket.send_json({
                            "type": "COMMAND",
                            "action": action,
                            "breakpoints": data.get("breakpoints", [])
                        })
                    except Exception:
                        unicorn_socket = None
                else:
                    await websocket.send_json({
                        "type": "CONSOLE_LOG",
                        "message": "⚠️ Hardware Unicorn disconnesso. Comando ignorato."
                    })
    except WebSocketDisconnect:
        ui_socket = None


@app.websocket("/telemetry")
async def telemetry_endpoint(websocket: WebSocket):
    global unicorn_socket, ui_socket
    await websocket.accept()
    unicorn_socket = websocket
    print("🛸 Emulatore Unicorn connesso alla Telemetria (/telemetry)")

    try:
        async for message in websocket.iter_text():
            if ui_socket:
                try:
                    data = json.loads(message)
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
                    ui_socket = None
    except WebSocketDisconnect:
        unicorn_socket = None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.backend.main:app", host="127.0.0.1", port=8001, log_level="info")
