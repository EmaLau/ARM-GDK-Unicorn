import asyncio
import json
import os
import re
import subprocess
import websockets
from nicegui import app, ui, background_tasks

# ---------------------------------------------------------------------------
# Stato condiviso del Debugger
# ---------------------------------------------------------------------------
debugger_state = {
    "pc": "0x10000",
    "registers": {f"r{i}": "0x0" for i in range(13)},
    "memory": {},
    "breakpoints": set(),
    "source_code": [],
    "raw_source_text": "",  # Contiene il testo grezzo per l'editor
    "console_output": ["[Sistema] Debugger pronto. In attesa del Backend centrale..."],
    "terminated": False,
    "active_regs": set()
}

# Cache di controllo per eliminare lo sfarfallio del DOM della pagina
_last_rendered_pc = None
_last_rendered_memory = None
_last_rendered_logs_len = 0
_last_rendered_breakpoints = set()

BASE_PC = 0x10000
EXIT_SENTINEL = 0xFFFF0000
SRC_FILE_PATH = "src/main.s"

# Client WebSocket per parlare con main.py
backend_ws = None
backend_ws_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Funzione di Salvataggio e Compilazione Automatica
# ---------------------------------------------------------------------------
def compile_arm_source(testo_sorgente: str = None):
    build_dir = "build"
    src_dir = "src"
    obj_file = os.path.join(build_dir, "main.o")
    elf_file = os.path.join(build_dir, "main.elf")

    os.makedirs(build_dir, exist_ok=True)
    os.makedirs(src_dir, exist_ok=True)

    if testo_sorgente is not None:
        with open(SRC_FILE_PATH, "w") as f:
            f.write(testo_sorgente)
        debugger_state["console_output"].append("[Editor] Modifiche salvate su src/main.s")

    if not os.path.exists(SRC_FILE_PATH):
        debugger_state["console_output"].append(f"[Compiler Errore] File {SRC_FILE_PATH} non trovato.")
        return False

    try:
        res_as = subprocess.run(["arm-none-eabi-as", "-o", obj_file, SRC_FILE_PATH], capture_output=True, text=True)
        if res_as.returncode != 0:
            debugger_state["console_output"].append(f"[Compiler Errore AS]: {res_as.stderr}")
            return False

        res_ld = subprocess.run(["arm-none-eabi-ld", "-Ttext", "0x10000", "-o", elf_file, obj_file],
                                capture_output=True, text=True)
        if res_ld.returncode != 0:
            debugger_state["console_output"].append(f"[Compiler Errore LD]: {res_ld.stderr}")
            return False

        debugger_state["console_output"].append("[Compiler Successo] Build completata con successo.")
        debugger_state["source_code"] = load_source_code()
        return True
    except FileNotFoundError:
        debugger_state["console_output"].append("[Compiler Errore] Toolchain 'arm-none-eabi-' non trovata.")
        return False
    except Exception as e:
        debugger_state["console_output"].append(f"[Compiler Errore Imprevisto]: {str(e)}")
        return False


# ---------------------------------------------------------------------------
# Lettura e Parsing del codice sorgente ARM
# ---------------------------------------------------------------------------
def load_source_code():
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

                if striped and not striped.startswith(("@", "//", "/*")) and not striped.endswith(":"):
                    if not striped.startswith("."):
                        is_instruction = True
                        addr_str = hex(current_pc)
                        current_pc += 4

                parsed_lines.append({
                    "line_num": idx + 1,
                    "text": clean_line,
                    "is_instruction": is_instruction,
                    "pc_addr": addr_str
                })
    else:
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


debugger_state["source_code"] = load_source_code()


def analizza_registri_attivi(testo_istruzione: str):
    registri_trovati = set()
    tokens = re.findall(r'\b(r[0-9]+|sp|lr|pc)\b', testo_istruzione.lower())
    for t in tokens:
        registri_trovati.add(t)
    return registri_trovati


async def send_command_to_backend(action: str):
    global backend_ws
    async with backend_ws_lock:
        ws = backend_ws
    if ws:
        payload = {
            "type": "SEND_COMMAND",
            "action": action,
            "breakpoints": list(debugger_state["breakpoints"])
        }
        try:
            await ws.send(json.dumps(payload))
        except Exception as e:
            print(f"[UI] Invio comando fallito: {e}")


async def maintain_backend_connection():
    global backend_ws
    uri = "ws://localhost:8001/ui"

    while True:
        try:
            async with websockets.connect(uri) as ws:
                async with backend_ws_lock:
                    backend_ws = ws
                debugger_state["console_output"].append("[Sistema] Connesso al Backend centrale.")

                # Sincronizza subito i breakpoint iniziali se presenti
                await send_command_to_backend('update_breakpoints')

                while True:
                    message = await ws.recv()
                    payload = json.loads(message)

                    if payload.get("type") == "UPDATE_STATE":
                        data = payload.get("data", {})

                        debugger_state["pc"] = data.get("pc", "0x10000")
                        debugger_state["registers"] = data.get("registers", debugger_state["registers"])
                        debugger_state["memory"] = data.get("memory", {})

                        if "console_output" in data and data["console_output"]:
                            debugger_state["console_output"].append(data["console_output"])

                        pc_corrente = debugger_state["pc"]
                        debugger_state["active_regs"] = set()
                        for row in debugger_state["source_code"]:
                            if row["is_instruction"] and row["pc_addr"] == pc_corrente:
                                debugger_state["active_regs"] = analizza_registri_attivi(row["text"])
                                break

                        if data.get("status") == "terminated":
                            debugger_state["terminated"] = True
                        else:
                            try:
                                debugger_state["terminated"] = int(debugger_state["pc"], 16) == EXIT_SENTINEL
                            except ValueError:
                                pass

                        if len(debugger_state["console_output"]) > 100:
                            debugger_state["console_output"] = debugger_state["console_output"][-100:]

        except Exception:
            await asyncio.sleep(2)


app.on_startup(lambda: background_tasks.create(maintain_backend_connection()))


# ---------------------------------------------------------------------------
# Interfaccia Grafica UI (NiceGUI Unificata)
# ---------------------------------------------------------------------------
@ui.page('/')
def index_page():
    ui.dark_mode().enable()
    ui.query('body').classes('bg-slate-900 text-slate-100 antialiased overflow-x-hidden')

    editor_textarea = None

    with ui.header().classes('bg-slate-950 border-b border-slate-800 px-6 py-3 justify-between items-center shadow-lg'):
        with ui.row().classes('items-center gap-3'):
            ui.icon('developer_board').classes('text-cyan-400 text-2xl')
            ui.label('ARM v7 Integrated Debugger & Editor').classes('text-lg font-black tracking-wider text-slate-100')

        with ui.row().classes('items-center gap-4'):
            ui.button('Salva & Compila', on_click=lambda: compile_arm_source(editor_textarea.value)).props(
                'flat color=orange icon=build text-xs')

            with ui.row().classes('bg-slate-800 p-1 rounded-lg border border-slate-700 gap-1'):
                # Continue esegue anche salvataggio e compilazione immediata
                async def handle_continue():
                    compile_arm_source(editor_textarea.value)
                    await send_command_to_backend('continue')

                btn_run = ui.button('Continue', on_click=lambda: background_tasks.create(handle_continue())).props(
                    'flat color=green icon=play_arrow text-xs')

                # NUOVO PULSANTE: Back Step (Passo Indietro)
                btn_back = ui.button('Back Step', on_click=lambda: background_tasks.create(
                    send_command_to_backend('back_step'))).props('flat color=deep-orange icon=undo text-xs')

                btn_step = ui.button('Step', on_click=lambda: background_tasks.create(
                    send_command_to_backend('step'))).props('flat color=amber icon=redo text-xs')

                btn_pause = ui.button('Pause', on_click=lambda: background_tasks.create(
                    send_command_to_backend('pause'))).props('flat color=red icon=pause text-xs')

                async def handle_restart():
                    compile_arm_source(editor_textarea.value)
                    debugger_state["terminated"] = False
                    debugger_state["pc"] = "0x10000"
                    debugger_state["active_regs"] = set()
                    debugger_state["console_output"].append("[Sistema] Reset, salvataggio e ricompilazione completati.")
                    await send_command_to_backend('restart')

                btn_restart = ui.button('Restart', on_click=lambda: background_tasks.create(handle_restart())).props(
                    'flat color=cyan icon=restart_alt text-xs'
                )

            pc_label = ui.label("PC: 0x10000").classes(
                'bg-slate-800 px-4 py-1.5 rounded-lg border border-cyan-500/30 text-cyan-400 font-mono text-sm')

    with ui.row().classes('w-full p-4 gap-4 items-stretch'):

        # COLONNA DI SINISTRA: Pannello a schede (Monitor Debugger FIRST)
        with ui.card().classes(
                'bg-slate-950 border border-slate-800 p-2 shadow-xl flex-[2] min-w-[550px] h-[580px] flex flex-col'):

            with ui.tabs().classes('w-full border-b border-slate-800') as tabs:
                tab_debug = ui.tab('🔍 Monitor Debugger')
                tab_editor = ui.tab('📝 Scrivi Codice (src/main.s)')

            with ui.tab_panels(tabs, value=tab_debug).classes('w-full bg-transparent flex-grow p-2 text-xs'):

                # TAB MONITOR STEP-BY-STEP
                with ui.tab_panel(tab_debug).classes('p-0 h-full overflow-y-auto') as debug_panel:
                    code_ui_rows = []

                    def render_debug_view():
                        debug_panel.clear()
                        code_ui_rows.clear()
                        with debug_panel:
                            with ui.column().classes('w-full gap-0 font-mono'):
                                for row in debugger_state["source_code"]:
                                    with ui.row().classes(
                                            'w-full py-0.5 px-2 items-center rounded gap-3 hover:bg-slate-900/40') as ui_row:

                                        # Funzione nativa per attivare/disattivare e inviare i breakpoint reali al backend
                                        def toggle_breakpoint(addr=row["pc_addr"]):
                                            if not addr: return
                                            if addr in debugger_state["breakpoints"]:
                                                debugger_state["breakpoints"].remove(addr)
                                                debugger_state["console_output"].append(
                                                    f"[Breakpoint] Rimosso all'indirizzo {addr}")
                                            else:
                                                debugger_state["breakpoints"].add(addr)
                                                debugger_state["console_output"].append(
                                                    f"[Breakpoint] Inserito all'indirizzo {addr}")

                                            # Comunica subito la variazione dei breakpoint attivi al backend centralizzato
                                            background_tasks.create(send_command_to_backend('update_breakpoints'))

                                        if row["is_instruction"]:
                                            bp_btn = ui.button(on_click=toggle_breakpoint).classes(
                                                'w-3 h-3 min-h-0 p-0 rounded-full cursor-pointer bg-slate-800 opacity-20')
                                        else:
                                            ui.label('').classes('w-3 h-3')
                                            bp_btn = None

                                        ui.label(f"{row['line_num']:02d}").classes(
                                            'text-slate-600 text-[10px] w-5 text-right select-none')
                                        ui.label(f"{row['pc_addr'] if row['is_instruction'] else '        '}").classes(
                                            'text-slate-500 text-[10px] w-12 select-none')
                                        txt_lbl = ui.label(row["text"]).classes(
                                            'whitespace-pre flex-grow text-slate-100')

                                        code_ui_rows.append({
                                            "pc_addr": row["pc_addr"],
                                            "is_instruction": row["is_instruction"],
                                            "container": ui_row,
                                            "bp_btn": bp_btn,
                                            "txt_lbl": txt_lbl
                                        })

                    render_debug_view()

                # TAB EDITOR
                with ui.tab_panel(tab_editor).classes('p-0 h-full flex flex-col'):
                    ui.label(
                        "Modifica direttamente il codice Assembly ARMv7 qui sotto, poi premi 'Salva & Compila':").classes(
                        'text-slate-400 mb-2 italic')
                    editor_textarea = ui.textarea(value=debugger_state["raw_source_text"]).classes(
                        'w-full flex-grow font-mono bg-slate-900 text-slate-100 p-4 rounded border border-slate-700 focus:border-cyan-500 resize-none h-[440px]'
                    ).props('square outlined dark')

        # COLONNA DI DESTRA: Registri + Dump Stack
        with ui.column().classes('flex-1 min-w-[350px] gap-4'):
            with ui.card().classes('bg-slate-800 border border-slate-700/50 p-4 shadow-xl w-full'):
                ui.label('Registri della CPU').classes('text-sm font-bold uppercase tracking-wider text-slate-400 mb-2')

                reg_containers = {}
                reg_labels = {}
                with ui.grid(columns=3).classes('w-full gap-2 text-sm font-mono'):
                    target_regs = [f"r{i}" for i in range(13)] + ["sp", "lr", "cpsr"]
                    for reg in target_regs:
                        with ui.column().classes(
                                'p-1 rounded bg-slate-900/40 border border-slate-700/30 items-center') as box:
                            ui.label(reg).classes('text-slate-400 font-bold uppercase text-[11px]')
                            lbl = ui.label("0x0").classes('text-emerald-400 font-bold text-xs')
                            reg_containers[reg] = box
                            reg_labels[reg] = lbl

            with ui.card().classes(
                    'bg-slate-800 border border-slate-700/50 p-4 shadow-xl w-full flex-grow h-[260px] overflow-y-auto'):
                ui.label('Dump dello Stack (Memoria)').classes(
                    'text-sm font-bold uppercase tracking-wider text-slate-400 mb-2')
                mem_rows_container = ui.column().classes('w-full gap-1 font-mono text-xs')

    # Console inferiore
    with ui.card().classes('w-full mx-4 mb-4 bg-slate-950 border border-slate-800 p-4 rounded-xl shadow-inner'):
        ui.label('Console Log Eventi / Output Simulatore').classes(
            'text-xs font-bold uppercase tracking-wider text-green-500 mb-2')
        log_column_container = ui.column().classes('w-full gap-0.5 font-mono text-xs max-h-[120px] overflow-y-auto')

    last_src_version = list(debugger_state["source_code"])

    # ---------------------------------------------------------------------------
    # Loop di Aggiornamento Reattivo Intelligente (Previene i refresh continui)
    # ---------------------------------------------------------------------------
    def update_ui_loop():
        global _last_rendered_pc, _last_rendered_memory, _last_rendered_logs_len, _last_rendered_breakpoints
        nonlocal last_src_version

        # Rigenera il layout delle istruzioni solo quando viene compilato un nuovo file binario
        if debugger_state["source_code"] != last_src_version:
            render_debug_view()
            last_src_version = list(debugger_state["source_code"])

        current_pc_str = debugger_state['pc']
        pc_label.set_text(f"PC attuale: {current_pc_str}")

        # Aggiornamento dello stile grafico dei Registri
        for reg, label in reg_labels.items():
            val = str(debugger_state["registers"].get(reg, "0x0"))
            label.set_text(val)
            if reg in debugger_state["active_regs"]:
                reg_containers[reg].classes('bg-amber-500/20 border-amber-500/80',
                                            remove='bg-slate-900/40 border-slate-700/30')
                label.classes('text-amber-400', remove='text-emerald-400')
            else:
                reg_containers[reg].classes('bg-slate-900/40 border-slate-700/30',
                                            remove='bg-amber-500/20 border-amber-500/80')
                label.classes('text-emerald-400', remove='text-amber-400')

        if debugger_state["terminated"]:
            btn_run.disable();
            btn_step.disable();
            btn_pause.disable();
            btn_back.disable()
        else:
            btn_run.enable();
            btn_step.enable();
            btn_pause.enable();
            btn_back.enable()

        # Evidenzia la riga corrente e aggiorna graficamente i Breakpoint se cambia il PC o la lista BP
        if current_pc_str != _last_rendered_pc or debugger_state["breakpoints"] != _last_rendered_breakpoints:
            for r_ui in code_ui_rows:
                is_current = (r_ui["pc_addr"] == current_pc_str and r_ui["is_instruction"])
                is_bp = r_ui["pc_addr"] in debugger_state["breakpoints"]

                if is_current:
                    r_ui["container"].classes('bg-amber-500/20 border-l-4 border-amber-500',
                                              remove='hover:bg-slate-900/40')
                    r_ui["txt_lbl"].classes('text-amber-300 font-bold', remove='text-slate-100 text-slate-400')
                else:
                    r_ui["container"].classes(remove='bg-amber-500/20 border-l-4 border-amber-500')
                    if r_ui["is_instruction"]:
                        r_ui["txt_lbl"].classes('text-slate-100', remove='text-amber-300 font-bold text-slate-400')
                    else:
                        r_ui["txt_lbl"].classes('text-slate-400', remove='text-amber-300 font-bold text-slate-100')

                if r_ui["bp_btn"]:
                    if is_bp:
                        r_ui["bp_btn"].classes('bg-red-600 shadow-[0_0_8px_rgba(220,38,38,0.7)] opacity-100',
                                               remove='bg-slate-800 opacity-20')
                    else:
                        r_ui["bp_btn"].classes('bg-slate-800 opacity-20',
                                               remove='bg-red-600 shadow-[0_0_8px_rgba(220,38,38,0.7)] opacity-100')
            _last_rendered_pc = current_pc_str
            _last_rendered_breakpoints = set(debugger_state["breakpoints"])

        # Svuota e aggiorna lo Stack solo se i dati in memoria sono effettivamente cambiati
        if debugger_state["memory"] != _last_rendered_memory:
            mem_rows_container.clear()
            sorted_memory = sorted(debugger_state["memory"].items(), key=lambda x: int(x[0], 16), reverse=True)
            with mem_rows_container:
                for addr, val in sorted_memory:
                    with ui.row().classes(
                            'justify-between w-full border-b border-slate-700/30 py-0.5 px-2 hover:bg-slate-800/50'):
                        ui.label(addr).classes('text-cyan-400 font-bold')
                        ui.label(str(val)).classes('text-slate-100 font-bold')
            _last_rendered_memory = dict(debugger_state["memory"])

        # Svuota e aggiorna la Console Log solo se ci sono nuovi messaggi in coda
        if len(debugger_state["console_output"]) != _last_rendered_logs_len:
            log_column_container.clear()
            with log_column_container:
                for msg in debugger_state["console_output"][-12:]:
                    if '[Output Emulatore]' in msg:
                        c = 'text-green-400 font-bold'
                    elif '[Fine Esecuzione]' in msg:
                        c = 'text-red-400 font-bold'
                    elif '[Breakpoint]' in msg:
                        c = 'text-amber-400 font-bold'
                    elif '[Compiler Errore]' in msg:
                        c = 'text-red-500 font-bold'
                    elif '[Compiler]' in msg:
                        c = 'text-sky-400 font-bold'
                    elif '[Editor]' in msg:
                        c = 'text-purple-400 font-bold'
                    else:
                        c = 'text-slate-400'
                    ui.label(f" {msg}").classes(c)
            _last_rendered_logs_len = len(debugger_state["console_output"])

    ui.timer(0.1, update_ui_loop)


ui.run(host='0.0.0.0', port=8080, title="ARM Custom Integrated Debugger", reload=False, show=True)