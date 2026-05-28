# app/frontend/run.py
# Interfaccia grafica del debugger ARMv7 realizzata con NiceGUI.
# Si connette al backend FastAPI via WebSocket e aggiorna l'UI in tempo reale.

import asyncio
import json
import os
import re

import websockets
from nicegui import app, ui, background_tasks

# ---------------------------------------------------------------------------
# Stato globale del debugger
# Contiene il PC corrente, i valori dei registri, la memoria, i breakpoint,
# il sorgente Assembly e il buffer dei messaggi della console.
# ---------------------------------------------------------------------------
debugger_state = {
    "pc": "0x10000",
    "registers": {f"r{i}": "0x0" for i in range(13)},
    "memory": {},
    "breakpoints": set(),
    "source_code": [],
    "raw_source_text": "",
    "console_output": ["[Sistema] Debugger pronto. In attesa del Backend centrale..."],
    "terminated": False,
    "active_regs": set()
}

# Cache per il rendering differenziale: evita ridisegni inutili dell'UI
# confrontando lo stato attuale con l'ultimo stato renderizzato.
_last_rendered_pc = None
_last_rendered_memory = None
_last_rendered_logs_len = 0
_last_rendered_breakpoints = set()

# Indirizzo base del programma in memoria (corrisponde a CODE_ADDRESS nel compiler)
BASE_PC = 0x10000
# Valore sentinella che indica la fine dell'esecuzione (indirizzo non valido)
EXIT_SENTINEL = 0xFFFF0000
# Percorso del file sorgente Assembly rispetto alla root del progetto
SRC_FILE_PATH = "src/main.s"

# Connessione WebSocket verso il backend; condivisa tra coroutine tramite lock
backend_ws = None
backend_ws_lock = asyncio.Lock()


async def send_command_to_backend(action: str, current_code: str = None):
    """
    Invia un comando al backend FastAPI tramite WebSocket.

    Parametri:
        action       -- stringa che identifica il comando (es. 'step', 'compile', 'restart')
        current_code -- codice sorgente Assembly da includere nel payload (solo per 'compile')
    """
    global backend_ws
    async with backend_ws_lock:
        ws = backend_ws
    if ws:
        payload = {
            "type": "SEND_COMMAND",
            "action": action,
            "breakpoints": list(debugger_state["breakpoints"])
        }
        if current_code is not None:
            payload["code"] = current_code
        try:
            await ws.send(json.dumps(payload))
        except Exception as e:
            print(f"[UI] Invio comando fallito: {e}")


def load_source_code():
    """
    Legge il file sorgente 'src/main.s' e costruisce la lista delle righe
    annotate con numero di riga, indirizzo PC calcolato staticamente e
    flag che indica se la riga contiene un'istruzione eseguibile.

    Se il file non esiste, restituisce un programma ARMv7 di esempio.

    Ritorna una lista di dizionari con i campi:
        line_num      -- numero di riga (1-based)
        text          -- testo della riga senza newline
        is_instruction -- True se la riga contiene un'istruzione Assembly
        pc_addr       -- indirizzo esadecimale della riga (stringa, es. '0x10000')
    """
    parsed_lines = []
    current_pc = BASE_PC

    if os.path.exists(SRC_FILE_PATH):
        with open(SRC_FILE_PATH, "r") as f:
            righe = f.readlines()
            debugger_state["raw_source_text"] = "".join(righe)

            for idx, line in enumerate(righe):
                clean_line = line.strip('\n')
                striped = clean_line.strip()
                is_instruction = False
                addr_str = ""

                # Una riga è un'istruzione se non è vuota, non è un commento,
                # non è una direttiva (inizia con '.') e non è un'etichetta (termina con ':')
                if striped and not striped.startswith(("@", "//", "/*")) and not striped.endswith(":"):
                    if not striped.startswith("."):
                        is_instruction = True
                        addr_str = hex(current_pc)
                        current_pc += 4  # Ogni istruzione ARM occupa 4 byte

                parsed_lines.append({
                    "line_num": idx + 1,
                    "text": clean_line,
                    "is_instruction": is_instruction,
                    "pc_addr": addr_str
                })
    else:
        # File sorgente assente: mostra un programma dimostrativo
        placeholder = (
            "@ Codice di Esempio ARMv7\n"
            ".global _start\n\n"
            "_start:\n"
            "    mov r0, #5\n"
            "    mov r1, #10\n"
            "    add r2, r0, r1\n"
            "    @ Segnale di fine per l'emulatore\n"
            "    mov r7, #1\n"
            "    svc 0\n"
        )
        debugger_state["raw_source_text"] = placeholder
        parsed_lines.append({"line_num": 1, "text": placeholder, "is_instruction": False, "pc_addr": ""})

    return parsed_lines


# Carica il sorgente all'avvio prima che la UI venga costruita
debugger_state["source_code"] = load_source_code()


def analizza_registri_attivi(testo_istruzione: str):
    """
    Estrae i nomi dei registri ARM (r0-r12, sp, lr, pc) presenti
    nel testo di un'istruzione, per evidenziarli nell'UI.

    Ritorna un insieme di stringhe (es. {'r0', 'r1'}).
    """
    registri_trovati = set()
    tokens = re.findall(r'\b(r[0-9]+|sp|lr|pc)\b', testo_istruzione.lower())
    for t in tokens:
        registri_trovati.add(t)
    return registri_trovati


async def maintain_backend_connection():
    """
    Coroutine persistente che mantiene attiva la connessione WebSocket
    verso il backend (ws://127.0.0.1:8001/ui).

    In caso di disconnessione attende 2 secondi e ritenta automaticamente.
    Per ogni messaggio ricevuto aggiorna lo stato globale del debugger:
    registri, memoria, PC, output console e flag di terminazione.
    """
    global backend_ws
    uri = "ws://127.0.0.1:8001/ui"

    while True:
        try:
            async with websockets.connect(uri) as ws:
                async with backend_ws_lock:
                    backend_ws = ws
                debugger_state["console_output"].append("[Sistema] Connesso al Backend centrale.")
                # Sincronizza i breakpoint già impostati al momento della connessione
                await send_command_to_backend('update_breakpoints')

                while True:
                    message = await ws.recv()
                    payload = json.loads(message)

                    # Il backend invia UPDATE_STATE (stato emulatore) o CONSOLE_LOG (messaggi testuali)
                    if payload.get("type") == "UPDATE_STATE" or payload.get("type") == "CONSOLE_LOG":
                        data = payload.get("data", payload)

                        if "pc" in data:
                            debugger_state["pc"] = data.get("pc", "0x10000")
                        if "registers" in data:
                            debugger_state["registers"] = data.get("registers", debugger_state["registers"])
                        if "memory" in data:
                            debugger_state["memory"] = data.get("memory", {})

                        # Accoda i messaggi di log nella console dell'UI
                        if "console_output" in data and data["console_output"]:
                            if isinstance(data["console_output"], list):
                                debugger_state["console_output"].extend(data["console_output"])
                            else:
                                debugger_state["console_output"].append(data["console_output"])
                        elif "message" in payload:
                            debugger_state["console_output"].append(payload["message"])

                        # Determina quali registri sono referenziati dall'istruzione corrente
                        pc_corrente = debugger_state["pc"]
                        debugger_state["active_regs"] = set()
                        for row in debugger_state["source_code"]:
                            if row["is_instruction"] and row["pc_addr"] == pc_corrente:
                                debugger_state["active_regs"] = analizza_registri_attivi(row["text"])
                                break

                        # Aggiorna il flag di terminazione in base allo status ricevuto
                        if data.get("status") == "terminated" or data.get("terminated"):
                            debugger_state["terminated"] = True
                        else:
                            try:
                                debugger_state["terminated"] = int(debugger_state["pc"], 16) == EXIT_SENTINEL
                            except ValueError:
                                pass

                        # Ricarica il sorgente per riflettere eventuali modifiche salvate
                        debugger_state["source_code"] = load_source_code()

                        # Limita il buffer della console a 100 messaggi per evitare memory leak
                        if len(debugger_state["console_output"]) > 100:
                            debugger_state["console_output"] = debugger_state["console_output"][-100:]

        except Exception:
            # Qualsiasi errore (connessione rifiutata, timeout, ecc.) causa un retry dopo 2s
            await asyncio.sleep(2)


# Avvia la coroutine di connessione al backend all'avvio dell'applicazione NiceGUI
app.on_startup(lambda: background_tasks.create(maintain_backend_connection()))


@ui.page('/')
def index_page():
    """
    Costruisce l'intera pagina del debugger:
      - Header con titolo, pulsanti di controllo e indicatore del PC
      - Pannello sinistro: tab "Monitor Debugger" (sorgente + breakpoint) e "Editor" (textarea)
      - Pannello destro: griglia registri CPU e dump dello stack
      - Footer: console log degli eventi
    L'aggiornamento dell'UI avviene ogni 100ms tramite ui.timer.
    """
    ui.dark_mode().enable()
    ui.query('body').classes('bg-slate-900 text-slate-100 antialiased overflow-x-hidden')

    # Riferimento all'editor di testo; viene assegnato più in basso e usato nelle lambda dei pulsanti
    editor_textarea = None

    # -----------------------------------------------------------------------
    # Header: titolo + controlli di esecuzione + indicatore PC
    # -----------------------------------------------------------------------
    with ui.header().classes('bg-slate-950 border-b border-slate-800 px-6 py-3 justify-between items-center shadow-lg'):
        with ui.row().classes('items-center gap-3'):
            ui.icon('developer_board').classes('text-cyan-400 text-2xl')
            ui.label('ARM v7 Integrated Debugger & Editor').classes('text-lg font-black tracking-wider text-slate-100')

        with ui.row().classes('items-center gap-4'):
            # Salva il codice sorgente su disco e avvia la compilazione via Keystone
            ui.button('Salva & Compila', on_click=lambda: background_tasks.create(
                send_command_to_backend('compile', editor_textarea.value))).props(
                'flat color=orange icon=build text-xs')

            with ui.row().classes('bg-slate-800 p-1 rounded-lg border border-slate-700 gap-1'):

                async def handle_continue():
                    # Prima ricompila per assicurarsi che il binario sia aggiornato, poi riprende
                    await send_command_to_backend('compile', editor_textarea.value)
                    await send_command_to_backend('continue')

                btn_run = ui.button('Continue', on_click=lambda: background_tasks.create(handle_continue())).props(
                    'flat color=green icon=play_arrow text-xs')

                btn_step = ui.button('Step',
                                     on_click=lambda: background_tasks.create(send_command_to_backend('step'))).props(
                    'flat color=amber icon=redo text-xs')

                btn_pause = ui.button('Pause',
                                      on_click=lambda: background_tasks.create(send_command_to_backend('pause'))).props(
                    'flat color=red icon=pause text-xs')

                async def handle_restart():
                    # Ricompila e reinizializza l'emulatore allo stato iniziale
                    await send_command_to_backend('compile', editor_textarea.value)
                    debugger_state["terminated"] = False
                    debugger_state["pc"] = "0x10000"
                    debugger_state["active_regs"] = set()
                    await send_command_to_backend('restart')

                btn_restart = ui.button('Restart', on_click=lambda: background_tasks.create(handle_restart())).props(
                    'flat color=cyan icon=restart_alt text-xs')

            # Etichetta che mostra il valore aggiornato del Program Counter
            pc_label = ui.label("PC: 0x10000").classes(
                'bg-slate-800 px-4 py-1.5 rounded-lg border border-cyan-500/30 text-cyan-400 font-mono text-sm')

    # -----------------------------------------------------------------------
    # Corpo principale: editor/monitor a sinistra, registri/memoria a destra
    # -----------------------------------------------------------------------
    with ui.row().classes('w-full p-4 gap-4 items-stretch'):

        # --- Pannello sinistro: tab con vista debug e editor del sorgente ---
        with ui.card().classes(
                'bg-slate-950 border border-slate-800 p-2 shadow-xl flex-[2] min-w-[550px] h-[580px] flex flex-col'):
            with ui.tabs().classes('w-full border-b border-slate-800') as tabs:
                tab_debug = ui.tab('Monitor Debugger')
                tab_editor = ui.tab('Scrivi Codice (src/main.s)')

            with ui.tab_panels(tabs, value=tab_debug).classes('w-full bg-transparent flex-grow p-2 text-xs'):

                # Tab 1: Vista del codice sorgente con evidenziazione del PC e breakpoint
                with ui.tab_panel(tab_debug).classes('p-0 h-full overflow-y-auto') as debug_panel:
                    code_ui_rows = []  # Lista dei componenti UI per aggiornamento differenziale

                    def render_debug_view():
                        """
                        Ridisegna l'intera vista del sorgente. Viene chiamata solo quando
                        il codice sorgente cambia (nuovo salvataggio/compilazione).
                        Ogni riga mostra: pallino breakpoint | numero riga | indirizzo PC | testo istruzione.
                        """
                        debug_panel.clear()
                        code_ui_rows.clear()
                        with debug_panel:
                            with ui.column().classes('w-full gap-0 font-mono'):
                                for row in debugger_state["source_code"]:
                                    with ui.row().classes(
                                            'w-full py-0.5 px-2 items-center rounded gap-3 hover:bg-slate-900/40') as ui_row:

                                        def toggle_breakpoint(addr=row["pc_addr"]):
                                            """Aggiunge o rimuove un breakpoint all'indirizzo fornito."""
                                            if not addr:
                                                return
                                            if addr in debugger_state["breakpoints"]:
                                                debugger_state["breakpoints"].remove(addr)
                                                debugger_state["console_output"].append(
                                                    f"[Breakpoint] Rimosso all'indirizzo {addr}")
                                            else:
                                                debugger_state["breakpoints"].add(addr)
                                                debugger_state["console_output"].append(
                                                    f"[Breakpoint] Inserito all'indirizzo {addr}")
                                            background_tasks.create(send_command_to_backend('update_breakpoints'))

                                        # Solo le righe con istruzione hanno il pulsante breakpoint cliccabile
                                        if row["is_instruction"]:
                                            bp_btn = ui.button(on_click=toggle_breakpoint).classes(
                                                'w-3 h-3 min-h-0 p-0 rounded-full cursor-pointer bg-slate-800 opacity-20')
                                        else:
                                            ui.label('').classes('w-3 h-3')
                                            bp_btn = None

                                        # Colonna numero di riga
                                        ui.label(f"{row['line_num']:02d}").classes(
                                            'text-slate-600 text-[10px] w-5 text-right select-none')
                                        # Colonna indirizzo PC (vuota per righe non-istruzione)
                                        ui.label(f"{row['pc_addr'] if row['is_instruction'] else '        '}").classes(
                                            'text-slate-500 text-[10px] w-12 select-none')
                                        # Testo dell'istruzione / commento / direttiva
                                        txt_lbl = ui.label(row["text"]).classes(
                                            'whitespace-pre flex-grow text-slate-100')

                                        # Mantieni riferimento ai componenti per l'aggiornamento differenziale
                                        code_ui_rows.append({
                                            "pc_addr": row["pc_addr"],
                                            "is_instruction": row["is_instruction"],
                                            "container": ui_row,
                                            "bp_btn": bp_btn,
                                            "txt_lbl": txt_lbl
                                        })

                    render_debug_view()

                # Tab 2: Textarea per modificare direttamente il sorgente Assembly
                with ui.tab_panel(tab_editor).classes('p-0 h-full flex flex-col'):
                    ui.label(
                        "Modifica direttamente il codice Assembly ARMv7 qui sotto, poi premi 'Salva & Compila':").classes(
                        'text-slate-400 mb-2 italic')
                    editor_textarea = ui.textarea(value=debugger_state["raw_source_text"]).classes(
                        'w-full flex-grow font-mono bg-slate-900 text-slate-100 p-4 rounded border border-slate-700 focus:border-cyan-500 resize-none h-[440px]'
                    ).props('square outlined dark')

        # --- Pannello destro: registri CPU e dump dello stack ---
        with ui.column().classes('flex-1 min-w-[350px] gap-4'):

            # Griglia dei registri ARM: r0-r12, sp, lr, pc, cpsr
            with ui.card().classes('bg-slate-800 border border-slate-700/50 p-4 shadow-xl w-full'):
                ui.label('Registri della CPU').classes('text-sm font-bold uppercase tracking-wider text-slate-400 mb-2')

                reg_containers = {}  # Contenitori dei box registro (per cambio colore)
                reg_labels = {}      # Etichette con il valore corrente del registro
                with ui.grid(columns=3).classes('w-full gap-2 text-sm font-mono'):
                    target_regs = [f"r{i}" for i in range(13)] + ["sp", "lr", "pc", "cpsr"]
                    for reg in target_regs:
                        with ui.column().classes(
                                'p-1 rounded bg-slate-900/40 border border-slate-700/30 items-center') as box:
                            ui.label(reg).classes('text-slate-400 font-bold uppercase text-[11px]')
                            lbl = ui.label("0x0").classes('text-emerald-400 font-bold text-xs')
                            reg_containers[reg] = box
                            reg_labels[reg] = lbl

            # Dump dello stack: mostra le word intorno allo Stack Pointer
            with ui.card().classes(
                    'bg-slate-800 border border-slate-700/50 p-4 shadow-xl w-full flex-grow h-[260px] overflow-y-auto'):
                ui.label('Dump dello Stack (Memoria)').classes(
                    'text-sm font-bold uppercase tracking-wider text-slate-400 mb-2')
                mem_rows_container = ui.column().classes('w-full gap-1 font-mono text-xs')

    # -----------------------------------------------------------------------
    # Footer: console log degli eventi del debugger
    # -----------------------------------------------------------------------
    with ui.card().classes('w-full mx-4 mb-4 bg-slate-950 border border-slate-800 p-4 rounded-xl shadow-inner'):
        ui.label('Console Log Eventi / Output Simulatore').classes(
            'text-xs font-bold uppercase tracking-wider text-green-500 mb-2')
        log_column_container = ui.column().classes('w-full gap-0.5 font-mono text-xs max-h-[120px] overflow-y-auto')

    # Versione precedente del sorgente per rilevare cambiamenti che richiedono ridisegno
    last_src_version = list(debugger_state["source_code"])

    def update_ui_loop():
        """
        Callback eseguita ogni 100ms dal timer NiceGUI.
        Implementa un aggiornamento differenziale: aggiorna solo i componenti
        il cui stato è cambiato dall'ultimo ciclo, evitando ridisegni costosi.

        Operazioni eseguite:
          1. Ridisegno del sorgente se il codice è cambiato
          2. Aggiornamento del PC nell'header
          3. Aggiornamento dei valori e colori dei registri
          4. Abilitazione/disabilitazione dei pulsanti in base allo stato di terminazione
          5. Evidenziazione del PC e dei breakpoint nel sorgente
          6. Aggiornamento del dump della memoria
          7. Aggiornamento della console log
        """
        global _last_rendered_pc, _last_rendered_memory, _last_rendered_logs_len, _last_rendered_breakpoints
        nonlocal last_src_version

        # 1. Ridisegno del sorgente solo se il codice è cambiato
        if debugger_state["source_code"] != last_src_version:
            render_debug_view()
            last_src_version = list(debugger_state["source_code"])

        # 2. Aggiornamento del PC nell'header
        current_pc_str = debugger_state['pc']
        pc_label.set_text(f"PC attuale: {current_pc_str}")

        # 3. Aggiornamento valori e colori dei registri
        for reg, label in reg_labels.items():
            val = str(debugger_state["registers"].get(reg, "0x0"))
            label.set_text(val)
            # I registri referenziati dall'istruzione corrente vengono evidenziati in arancione
            if reg in debugger_state["active_regs"]:
                reg_containers[reg].classes('bg-amber-500/20 border-amber-500/80',
                                            remove='bg-slate-900/40 border-slate-700/30')
                label.classes('text-amber-400', remove='text-emerald-400')
            else:
                reg_containers[reg].classes('bg-slate-900/40 border-slate-700/30',
                                            remove='bg-amber-500/20 border-amber-500/80')
                label.classes('text-emerald-400', remove='text-amber-400')

        # 4. Disabilita i controlli di esecuzione quando il programma è terminato
        if debugger_state["terminated"]:
            btn_run.disable()
            btn_step.disable()
            btn_pause.disable()
        else:
            btn_run.enable()
            btn_step.enable()
            btn_pause.enable()

        # 5. Evidenziazione del PC corrente e dei breakpoint nel sorgente (solo se cambiati)
        if current_pc_str != _last_rendered_pc or debugger_state["breakpoints"] != _last_rendered_breakpoints:
            for r_ui in code_ui_rows:
                is_current = (r_ui["pc_addr"] == current_pc_str and r_ui["is_instruction"])
                is_bp = r_ui["pc_addr"] in debugger_state["breakpoints"]

                if is_current:
                    # Riga attiva: sfondo arancione e testo in grassetto
                    r_ui["container"].classes('bg-amber-500/20 border-l-4 border-amber-500',
                                              remove='hover:bg-slate-900/40')
                    r_ui["txt_lbl"].classes('text-amber-300 font-bold', remove='text-slate-100 text-slate-400')
                else:
                    r_ui["container"].classes(remove='bg-amber-500/20 border-l-4 border-amber-500')
                    if r_ui["is_instruction"]:
                        r_ui["txt_lbl"].classes('text-slate-100', remove='text-amber-300 font-bold text-slate-400')
                    else:
                        r_ui["txt_lbl"].classes('text-slate-400', remove='text-amber-300 font-bold text-slate-100')

                # Aggiorna il colore del pallino breakpoint (rosso = attivo, grigio = inattivo)
                if r_ui["bp_btn"]:
                    if is_bp:
                        r_ui["bp_btn"].classes('bg-red-600 shadow-[0_0_8px_rgba(220,38,38,0.7)] opacity-100',
                                               remove='bg-slate-800 opacity-20')
                    else:
                        r_ui["bp_btn"].classes('bg-slate-800 opacity-20',
                                               remove='bg-red-600 shadow-[0_0_8px_rgba(220,38,38,0.7)] opacity-100')
            _last_rendered_pc = current_pc_str
            _last_rendered_breakpoints = set(debugger_state["breakpoints"])

        # 6. Aggiornamento del dump della memoria (solo se il contenuto è cambiato)
        if debugger_state["memory"] != _last_rendered_memory:
            mem_rows_container.clear()
            # Ordine decrescente per indirizzo: la cima dello stack appare in alto
            sorted_memory = sorted(debugger_state["memory"].items(), key=lambda x: int(x[0], 16), reverse=True)
            with mem_rows_container:
                for addr, val in sorted_memory:
                    with ui.row().classes(
                            'justify-between w-full border-b border-slate-700/30 py-0.5 px-2 hover:bg-slate-800/50'):
                        ui.label(addr).classes('text-cyan-400 font-bold')
                        ui.label(str(val)).classes('text-slate-100 font-bold')
            _last_rendered_memory = dict(debugger_state["memory"])

        # 7. Aggiornamento della console log (solo se sono arrivati nuovi messaggi)
        if len(debugger_state["console_output"]) != _last_rendered_logs_len:
            log_column_container.clear()
            with log_column_container:
                # Mostra solo gli ultimi 12 messaggi per non sovraffollare la console
                for msg in debugger_state["console_output"][-12:]:
                    # Colore del messaggio in base alla categoria
                    if '[Output Emulatore]' in msg or '[Compiler SUCCESS]' in msg:
                        c = 'text-green-400 font-bold'
                    elif '[Fine Esecuzione]' in msg or '[Compiler Errore]' in msg:
                        c = 'text-red-400 font-bold'
                    elif '[Breakpoint]' in msg:
                        c = 'text-amber-400 font-bold'
                    elif '[Compiler]' in msg:
                        c = 'text-sky-400 font-bold'
                    elif '[Editor]' in msg:
                        c = 'text-purple-400 font-bold'
                    else:
                        c = 'text-slate-400'
                    ui.label(f" {msg}").classes(c)
            _last_rendered_logs_len = len(debugger_state["console_output"])

    # Avvia il loop di aggiornamento dell'UI a 10 fps (100ms)
    ui.timer(0.1, update_ui_loop)


ui.run(host='0.0.0.0', port=8080, title="ARM Custom Integrated Debugger", reload=False, show=True)
