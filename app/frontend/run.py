# app/frontend/run.py
# Interfaccia grafica del debugger ARMv7 realizzata con NiceGUI.
# Si connette al backend FastAPI via WebSocket e aggiorna l'UI in tempo reale.

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

import websockets
import websockets.exceptions
from nicegui import app, ui, background_tasks

# Aggiunge la root del progetto al path per importare i percorsi condivisi
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from app.paths import BUILD_LINES, ensure_workspace

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("interfaccia")

# ---------------------------------------------------------------------------
# Stato globale del debugger
# Contiene il PC corrente, i valori dei registri, la memoria, i breakpoint,
# il sorgente Assembly e il buffer dei messaggi della console.
# ---------------------------------------------------------------------------
debugger_state = {
    "pc": "0x10000",
    "registers": {f"r{i}": "0x0" for i in range(13)},
    "memory": {},
    # Parole della sezione .data/.bss: [{"addr", "section", "label", "value", "ascii"}, ...]
    "data_memory": [],
    "breakpoints": set(),
    "source_code": [],
    "raw_source_text": "",
    "console_output": ["[Sistema] Debugger pronto. In attesa del Backend centrale..."],
    "terminated": False,
    # Velocità dell'esecuzione continua in istruzioni al secondo (0 = massima)
    "speed": 5,
    "active_regs": set()
}

# Flag di condizione del CPSR: bit, nome e significato (mostrato come suggerimento)
CPSR_FLAGS = [
    ("N", 31, "Negative: il risultato dell'ultima operazione con S era negativo (bit 31 = 1)"),
    ("Z", 30, "Zero: il risultato dell'ultima operazione con S era zero"),
    ("C", 29, "Carry: riporto in un'addizione, nessun prestito in una sottrazione, o bit uscito da uno shift"),
    ("V", 28, "oVerflow: overflow con segno (il risultato non sta in 32 bit con segno)"),
]

# Velocità selezionabili per l'esecuzione continua (istruzioni al secondo, 0 = massima)
SPEED_OPTIONS = {1: "1 istr/s", 5: "5 istr/s", 20: "20 istr/s", 100: "100 istr/s", 0: "Massima"}
# Copia di lavoro del sorgente Assembly (workspace/main.s, creata dall'esempio se manca)
SRC_FILE_PATH = ensure_workspace()

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
            "breakpoints": list(debugger_state["breakpoints"]),
            "speed": debugger_state["speed"]
        }
        if current_code is not None:
            payload["code"] = current_code
        try:
            await ws.send(json.dumps(payload))
        except Exception as e:
            log.warning("Invio del comando '%s' al backend fallito: %s", action, e)


def load_source_code():
    """
    Legge il file sorgente 'workspace/main.s' e costruisce la lista delle righe
    annotate con numero di riga, indirizzo dell'istruzione e flag che indica se
    la riga contiene un'istruzione eseguibile.

    Gli indirizzi vengono da build/lines.json, scritto dal compilatore a partire
    dalle informazioni di debug dell'assembler GNU: sono quelli reali, anche con
    etichette sulla stessa riga, costanti o dati nel mezzo del codice.

    Se il file non esiste, restituisce un programma ARMv7 di esempio.

    Ritorna una lista di dizionari con i campi:
        line_num      -- numero di riga (1-based)
        text          -- testo della riga senza newline
        is_instruction -- True se la riga contiene un'istruzione Assembly
        pc_addr       -- indirizzo esadecimale della riga (stringa, es. '0x10000')
    """
    parsed_lines = []

    # Riga (stringa, 1-based) -> indirizzo; vuoto se il programma non è compilato
    try:
        line_map = json.loads(BUILD_LINES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        line_map = {}

    if os.path.exists(SRC_FILE_PATH):
        with open(SRC_FILE_PATH, "r", encoding="utf-8") as f:
            righe = f.readlines()
            debugger_state["raw_source_text"] = "".join(righe)

            for idx, line in enumerate(righe):
                clean_line = line.strip('\n')
                addr_str = line_map.get(str(idx + 1), "")
                is_instruction = bool(addr_str)

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


def handle_backend_message(payload: dict):
    """Aggiorna lo stato del debugger con un messaggio del backend (UPDATE_STATE o CONSOLE_LOG)."""
    # Il backend invia UPDATE_STATE (stato emulatore) o CONSOLE_LOG (messaggi testuali)
    if payload.get("type") == "UPDATE_STATE" or payload.get("type") == "CONSOLE_LOG":
        data = payload.get("data", payload)

        if "pc" in data:
            debugger_state["pc"] = data.get("pc", "0x10000")
        if "registers" in data:
            debugger_state["registers"] = data.get("registers", debugger_state["registers"])
        if "memory" in data:
            debugger_state["memory"] = data.get("memory", {})
        if "data_memory" in data:
            debugger_state["data_memory"] = data.get("data_memory") or []

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

        # Lo stato di terminazione lo decide solo l'emulatore: i messaggi del
        # compilatore (CONSOLE_LOG) non hanno 'status' e non lo cambiano
        if "status" in data:
            debugger_state["terminated"] = data["status"] == "terminated"

        # Ricarica il sorgente per riflettere eventuali modifiche salvate
        debugger_state["source_code"] = load_source_code()

        # Limita il buffer della console a 100 messaggi per evitare memory leak
        if len(debugger_state["console_output"]) > 100:
            debugger_state["console_output"] = debugger_state["console_output"][-100:]


async def maintain_backend_connection():
    """
    Coroutine persistente che mantiene attiva la connessione WebSocket
    verso il backend (ws://127.0.0.1:8001/ui).

    In caso di disconnessione attende 2 secondi e ritenta automaticamente.
    Ogni messaggio ricevuto aggiorna lo stato del debugger tramite
    handle_backend_message; un messaggio difettoso viene registrato nel log
    senza far cadere la connessione.
    """
    global backend_ws
    uri = "ws://127.0.0.1:8001/ui"
    connected_once = False

    while True:
        try:
            async with websockets.connect(uri) as ws:
                async with backend_ws_lock:
                    backend_ws = ws
                connected_once = True
                log.info("Connesso al backend")
                debugger_state["console_output"].append("[Sistema] Connesso al Backend centrale.")
                # Sincronizza i breakpoint già impostati al momento della connessione
                await send_command_to_backend('update_breakpoints')

                while True:
                    message = await ws.recv()
                    try:
                        handle_backend_message(json.loads(message))
                    except Exception:
                        log.exception("Messaggio del backend non gestito: %.200s", message)

        except (OSError, websockets.exceptions.WebSocketException) as e:
            # Backend non ancora avviato o connessione persa: si riprova ogni 2 secondi,
            # registrando solo il cambio di stato per non riempire il log
            if connected_once:
                log.warning("Connessione al backend persa (%s): riprovo", e)
                debugger_state["console_output"].append("[Sistema] Connessione al Backend persa: riprovo...")
                connected_once = False
            await asyncio.sleep(2)
        except Exception:
            log.exception("Errore inatteso nella connessione al backend: riprovo tra 2 secondi")
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
            # Salva il codice, lo compila con la toolchain GNU e, se va a buon fine,
            # il backend lo carica subito nell'emulatore (che riparte dall'inizio)
            ui.button('Salva & Compila', on_click=lambda: background_tasks.create(
                send_command_to_backend('compile', editor_textarea.value))).props(
                'flat color=orange icon=build text-xs')
            unsaved_badge = ui.label('● Modifiche non salvate').classes(
                'text-amber-400 text-xs font-bold').tooltip(
                "L'emulatore esegue l'ultima versione compilata: premi 'Salva & Compila' per usare quella nell'editor")

            def warn_if_unsaved():
                # Continue, Step e Restart non compilano: se l'editor è cambiato lo si ricorda
                if editor_textarea.value != debugger_state["raw_source_text"]:
                    debugger_state["console_output"].append(
                        "[Avviso] L'editor ha modifiche non salvate: l'emulatore esegue l'ultima versione "
                        "compilata. Premi 'Salva & Compila' per usarle.")

            def run_command(action):
                warn_if_unsaved()
                background_tasks.create(send_command_to_backend(action))

            with ui.row().classes('bg-slate-800 p-1 rounded-lg border border-slate-700 gap-1'):

                btn_run = ui.button('Continue', on_click=lambda: run_command('continue')).props(
                    'flat color=green icon=play_arrow text-xs')

                btn_step = ui.button('Step', on_click=lambda: run_command('step')).props(
                    'flat color=amber icon=redo text-xs')

                btn_pause = ui.button('Pause',
                                      on_click=lambda: background_tasks.create(send_command_to_backend('pause'))).props(
                    'flat color=red icon=pause text-xs')

                def handle_restart():
                    # Riporta all'inizio l'ultimo programma compilato (senza ricompilare)
                    debugger_state["terminated"] = False
                    debugger_state["active_regs"] = set()
                    run_command('restart')

                btn_restart = ui.button('Restart', on_click=handle_restart).props(
                    'flat color=cyan icon=restart_alt text-xs')

            def handle_speed(e):
                # La nuova velocità vale subito, anche durante l'esecuzione continua
                debugger_state["speed"] = e.value
                background_tasks.create(send_command_to_backend('set_speed'))

            ui.select(SPEED_OPTIONS, value=debugger_state["speed"], label='Velocità',
                      on_change=handle_speed).props('dense dark options-dense').classes('w-32 text-xs')

            def handle_close():
                # Chiude l'interfaccia; il lanciatore (app/launcher.py) se ne accorge
                # e chiude anche backend ed emulatore
                ui.notify('Debugger chiuso: puoi chiudere questa scheda.', type='info', timeout=0)
                ui.timer(0.5, app.shutdown, once=True)

            ui.button('Chiudi', on_click=handle_close).props('flat color=grey icon=power_settings_new text-xs')

            # Etichetta che mostra il valore aggiornato del Program Counter
            pc_label = ui.label("PC: 0x10000").classes(
                'bg-slate-800 px-4 py-1.5 rounded-lg border border-cyan-500/30 text-cyan-400 font-mono text-sm')

    # -----------------------------------------------------------------------
    # Corpo principale: editor/monitor a sinistra, registri/memoria a destra
    # -----------------------------------------------------------------------
    with ui.row().classes('w-full p-4 gap-4 items-stretch'):

        # --- Pannello sinistro: tab con vista debug e editor del sorgente ---
        with ui.card().classes(
                # Alto quanto la colonna di destra (self-stretch). flex-nowrap: con l'a capo, un
                # sorgente più alto del riquadro finiva in una seconda colonna fuori vista
                'bg-slate-950 border border-slate-800 p-2 shadow-xl flex-[2] min-w-[550px] min-h-[580px] self-stretch flex flex-col flex-nowrap'):
            with ui.tabs(value='Monitor Debugger').classes('w-full border-b border-slate-800') as tabs:
                ui.tab('Monitor Debugger')
                ui.tab('Scrivi Codice (workspace/main.s)')

            # Due contenitori mostrati e nascosti dalle schede; min-h-0 permette all'area di
            # restare alta quanto il riquadro e di far scorrere il sorgente al suo interno
            with ui.element('div').classes('w-full bg-transparent flex-grow min-h-0 p-2 text-xs overflow-hidden'):

                # Tab 1: Vista del codice sorgente con evidenziazione del PC e breakpoint
                with ui.column().classes('w-full p-0 h-full overflow-y-auto gap-0') as debug_panel:
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
                                            # color=None: senza, Quasar aggiunge bg-primary e il pallino
                                            # resta blu anche quando il breakpoint è attivo (rosso)
                                            bp_btn = ui.button(on_click=toggle_breakpoint, color=None).classes(
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
                with ui.column().classes('w-full p-0 h-full flex flex-col gap-0') as editor_panel:
                    ui.label(
                        "Modifica il codice Assembly ARMv7 qui sotto, poi premi 'Salva & Compila': il programma "
                        "viene caricato nell'emulatore. Continue, Step e Restart non ricompilano.").classes(
                        'text-slate-400 mb-2 italic')
                    editor_textarea = ui.textarea(value=debugger_state["raw_source_text"]).classes(
                        'w-full flex-grow font-mono bg-slate-900 text-slate-100 p-4 rounded border border-slate-700 focus:border-cyan-500 resize-none h-[440px]'
                    ).props('square outlined dark input-style="height: max(400px, calc(100vh - 330px)); resize: none"')  # area di testo alta quanto il riquadro

            debug_panel.bind_visibility_from(tabs, 'value', backward=lambda v: v == 'Monitor Debugger')
            editor_panel.bind_visibility_from(tabs, 'value', backward=lambda v: v != 'Monitor Debugger')

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

                # Flag di condizione del CPSR, accesi quando il bit vale 1
                flag_labels = {}
                with ui.row().classes('w-full items-center gap-2 mt-3'):
                    ui.label('Flag').classes('text-slate-400 font-bold uppercase text-[11px] mr-1')
                    for name, bit, meaning in CPSR_FLAGS:
                        flag_labels[name] = ui.label(name).classes(
                            'font-mono font-bold text-xs px-2 py-0.5 rounded border border-slate-600 '
                            'text-slate-500 bg-slate-900/40').tooltip(f"{meaning} (bit {bit} del CPSR)")
                    flags_text = ui.label('').classes('text-slate-500 text-[11px] font-mono ml-2')

            # Dump dello stack: mostra le word intorno allo Stack Pointer
            with ui.card().classes(
                    'bg-slate-800 border border-slate-700/50 p-4 shadow-xl w-full flex-grow h-[260px] overflow-y-auto'):
                ui.label('Dump dello Stack (Memoria)').classes(
                    'text-sm font-bold uppercase tracking-wider text-slate-400 mb-2')
                mem_rows_container = ui.column().classes('w-full gap-1 font-mono text-xs')

            # Memoria della sezione .data (e .bss): parole con etichetta, valore e caratteri
            with ui.card().classes(
                    'bg-slate-800 border border-slate-700/50 p-4 shadow-xl w-full h-[240px] overflow-y-auto'):
                ui.label('Memoria .data').classes(
                    'text-sm font-bold uppercase tracking-wider text-slate-400 mb-2')
                data_rows_container = ui.column().classes('w-full gap-1 font-mono text-xs')

    # -----------------------------------------------------------------------
    # Footer: console log degli eventi del debugger
    # -----------------------------------------------------------------------
    with ui.card().classes('w-full mx-4 mb-4 bg-slate-950 border border-slate-800 p-4 rounded-xl shadow-inner'):
        ui.label('Console Log Eventi / Output Simulatore').classes(
            'text-xs font-bold uppercase tracking-wider text-green-500 mb-2')
        log_column_container = ui.column().classes('w-full gap-0.5 font-mono text-xs max-h-[120px] overflow-y-auto')

    # Cache del ridisegno differenziale, propria di questa scheda: con più schede aperte
    # ognuna confronta lo stato con quello che ha disegnato lei
    last_src_version = list(debugger_state["source_code"])
    last_pc = None
    last_breakpoints = set()
    last_memory = None
    last_data_memory = None
    last_logs_key = None
    last_cpsr = None
    # Testo del file salvato quando l'editor di questa scheda è stato sincronizzato
    editor_base = debugger_state["raw_source_text"]

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
        nonlocal last_src_version, last_pc, last_breakpoints, last_memory, last_data_memory
        nonlocal last_logs_key, last_cpsr, editor_base

        # 0. Editor: se il file salvato è cambiato (es. compilato da un'altra scheda) e
        #    qui non ci sono modifiche in corso, l'editor mostra la nuova versione
        saved = debugger_state["raw_source_text"]
        if saved != editor_base:
            if editor_textarea.value == editor_base:
                editor_textarea.value = saved
            editor_base = saved
        unsaved_badge.set_visibility(editor_textarea.value != saved)

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
        if current_pc_str != last_pc or debugger_state["breakpoints"] != last_breakpoints:
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
            last_pc = current_pc_str
            last_breakpoints = set(debugger_state["breakpoints"])

        # 6. Aggiornamento del dump della memoria (solo se il contenuto è cambiato)
        if debugger_state["memory"] != last_memory:
            mem_rows_container.clear()
            # Ordine decrescente per indirizzo: la cima dello stack appare in alto
            sorted_memory = sorted(debugger_state["memory"].items(), key=lambda x: int(x[0], 16), reverse=True)
            with mem_rows_container:
                for addr, val in sorted_memory:
                    with ui.row().classes(
                            'justify-between w-full border-b border-slate-700/30 py-0.5 px-2 hover:bg-slate-800/50'):
                        ui.label(addr).classes('text-cyan-400 font-bold')
                        ui.label(str(val)).classes('text-slate-100 font-bold')
            last_memory = dict(debugger_state["memory"])

        # 6b. Memoria .data (solo se cambiata)
        if debugger_state["data_memory"] != last_data_memory:
            data_rows_container.clear()
            with data_rows_container:
                if not debugger_state["data_memory"]:
                    ui.label('Il programma non ha dati in .data o .bss.').classes('text-slate-500 italic')
                for row in debugger_state["data_memory"]:
                    with ui.row().classes(
                            'w-full items-center gap-3 border-b border-slate-700/30 py-0.5 px-2 hover:bg-slate-800/50'):
                        ui.label(row["addr"]).classes('text-cyan-400 font-bold w-20')
                        ui.label(row["label"]).classes('text-amber-300 font-bold w-16 truncate')
                        ui.label(row["value"]).classes('text-slate-100 font-bold')
                        ui.label(row["ascii"]).classes('text-slate-500 whitespace-pre')
            last_data_memory = list(debugger_state["data_memory"])

        # 6c. Flag del CPSR
        cpsr_str = debugger_state["registers"].get("cpsr", "0x0")
        if cpsr_str != last_cpsr:
            try:
                cpsr = int(cpsr_str, 16)
            except (TypeError, ValueError):
                cpsr = 0
            active = []
            for name, bit, _ in CPSR_FLAGS:
                on = bool(cpsr >> bit & 1)
                if on:
                    active.append(name)
                    flag_labels[name].classes('text-slate-900 bg-emerald-400 border-emerald-300',
                                              remove='text-slate-500 bg-slate-900/40 border-slate-600')
                else:
                    flag_labels[name].classes('text-slate-500 bg-slate-900/40 border-slate-600',
                                              remove='text-slate-900 bg-emerald-400 border-emerald-300')
            flags_text.set_text(" ".join(active) if active else "nessun flag attivo")
            last_cpsr = cpsr_str

        # 7. Aggiornamento della console log (solo se sono arrivati nuovi messaggi)
        logs = debugger_state["console_output"]
        logs_key = (len(logs), id(logs[-1]) if logs else None)
        if logs_key != last_logs_key:
            log_column_container.clear()
            with log_column_container:
                # Mostra solo gli ultimi 12 messaggi per non sovraffollare la console
                for msg in debugger_state["console_output"][-12:]:
                    # Colore del messaggio in base alla categoria
                    if '[Output Emulatore' in msg or '[Compiler SUCCESS]' in msg:
                        c = 'text-green-400 font-bold'
                    elif 'Eccezione' in msg or '[Compiler Errore]' in msg:
                        c = 'text-red-400 font-bold'
                    elif '[Fine Esecuzione]' in msg:
                        c = 'text-sky-400 font-bold'
                    elif '[Compiler Avviso]' in msg or '[Avviso]' in msg:
                        c = 'text-amber-400 font-bold'
                    elif '[Breakpoint]' in msg:
                        c = 'text-amber-400 font-bold'
                    elif '[Compiler]' in msg:
                        c = 'text-sky-400 font-bold'
                    elif '[Editor]' in msg:
                        c = 'text-purple-400 font-bold'
                    else:
                        c = 'text-slate-400'
                    # whitespace-pre-wrap: spazi e a capo dell'output del programma restano visibili
                    ui.label(f" {msg}").classes(c + ' whitespace-pre-wrap')
            last_logs_key = logs_key

    # Avvia il loop di aggiornamento dell'UI a 10 fps (100ms)
    ui.timer(0.1, update_ui_loop)


# Solo in locale: con 0.0.0.0 chiunque sulla stessa rete potrebbe aprire il debugger
# ARM_GDK_NO_BROWSER=1 evita di aprire il browser all'avvio (utile per prove e CI)
ui.run(host='127.0.0.1', port=8080, title="ARM Custom Integrated Debugger", reload=False,
       show=os.environ.get("ARM_GDK_NO_BROWSER") != "1")
