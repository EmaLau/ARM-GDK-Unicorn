import asyncio
import json
import websockets
from nicegui import ui

# Stato locale della UI per memorizzare i dati ricevuti dal backend
stato_registri = {reg: 0 for reg in ["R0", "R1", "R2", "R7", "PC", "SP"]}
output_console = ""

# Riferimenti agli elementi della UI che dovremo aggiornare dinamicamente
labels_registri = {}
log_view = None
status_label = None
ws_client = None


async def connetti_al_backend():
    """Stabilisce la connessione WebSocket con il backend FastAPI montato internamente."""
    global ws_client, status_label, log_view, labels_registri

    uri = "ws://127.0.0.1:8080/api/ws/debugger"

    while True:
        try:
            if status_label:
                status_label.set_text("🔄 Connessione in corso...")

            async with websockets.connect(uri) as ws:
                ws_client = ws
                if status_label:
                    status_label.set_text("🟢 Connesso al Backend")

                # Loop continuo di ascolto dei messaggi dal backend
                async for msg_str in ws:
                    msg = json.loads(msg_str)

                    if msg["type"] == "update":
                        # Aggiorna lo stato dei registri nella UI
                        for reg, val in msg["registers"].items():
                            stato_registri[reg] = val
                            if reg in labels_registri and labels_registri[reg]:
                                labels_registri[reg].set_text(f"{val} ({hex(val)})")

                        # Aggiorna la console
                        if log_view:
                            log_view.set_content(f"```text\n{msg['console']}\n```")
                        if status_label:
                            status_label.set_text(f"🟢 {msg['status']}")

                    elif msg["type"] == "error":
                        ui.notify(msg["message"], type="negative")

        except Exception as e:
            if status_label:
                status_label.set_text("🔴 Errore di connessione. Riprovo tra 2 secondi...")
            await asyncio.sleep(2)


async def invia_comando(comando: str):
    """Invia un comando (step/reset) tramite il WebSocket attivo."""
    global ws_client
    if ws_client:
        try:
            await ws_client.send(json.dumps({"command": comando}))
        except Exception:
            ui.notify("Errore nell'invio del comando!", type="negative")
    else:
        ui.notify("Backend non connesso!", type="warning")


# --- FUNZIONE COSTRUTTRICE DELL'INTERFACCIA GRAFICA ---

def inizializza_interfaccia():
    """Costruisce il layout della pagina ereditando l'estetica originale."""
    global log_view, status_label, labels_registri

    # Tema scuro/moderno ciano originale
    ui.colors(primary='#38bdf8', dark='#0f172a')

    # Header superiore
    with ui.header().classes('items-center justify-between bg-slate-900 text-white p-4'):
        ui.label('🧠 Custom ARMv7 Debugger').classes('text-xl font-bold')
        status_label = ui.label('Inizializzazione...')

    # Layout a due colonne (1/3 e 2/3)
    with ui.row().classes('w-full p-4 gap-4 no-wrap'):
        # COLONNA SINISTRA: Pannello di Controllo e Registri
        with ui.card().classes('w-1/3 p-4 bg-slate-100 dark:bg-slate-800'):
            ui.label('🎛️ Controlli').classes('text-lg font-bold mb-2')

            with ui.row().classes('gap-2 w-full justify-start mb-4'):
                ui.button('STEP OVER', on_click=lambda: invia_comando('step')).props(
                    'color=primary text-white icon=play_arrow')
                ui.button('RESET', on_click=lambda: invia_comando('reset')).props(
                    'color=red text-white icon=refresh')

            ui.separator().classes('my-2')

            ui.label('📊 Registri CPU').classes('text-lg font-bold mb-2')

            with ui.grid(columns=2).classes('w-full gap-2'):
                for reg in ["R0", "R1", "R2", "R7", "PC", "SP"]:
                    with ui.row().classes(
                            'items-center bg-white dark:bg-slate-700 p-2 rounded shadow-sm justify-between'):
                        ui.label(reg).classes('font-mono font-bold text-slate-500')
                        labels_registri[reg] = ui.label("0 (0x0)").classes('font-mono')

        # COLONNA DESTRA: Output della Console dell'Emulatore
        with ui.card().classes('w-2/3 p-4 bg-slate-100 dark:bg-slate-800 flex-grow'):
            ui.label('🖥️ Output Console (Emulatore Linux)').classes('text-lg font-bold mb-2')
            log_view = ui.markdown('```text\n\n```').classes(
                'w-full p-2 bg-black text-green-400 rounded font-mono min-h-[200px]')

    # Avvia la connessione in background subito dopo aver disegnato i componenti
    ui.timer(0.1, connetti_al_backend, once=True)