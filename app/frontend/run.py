# app/frontend/run.py
import asyncio
import json
import os
import re

import websockets
from nicegui import app, ui, background_tasks

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

_last_rendered_pc = None
_last_rendered_memory = None
_last_rendered_logs_len = 0
_last_rendered_breakpoints = set()

BASE_PC = 0x10000
EXIT_SENTINEL = 0xFFFF0000
SRC_FILE_PATH = "src/main.s"

backend_ws = None
backend_ws_lock = asyncio.Lock()


async def send_command_to_backend(action: str, current_code: str = None):
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

async def maintain_backend_connection():
    global backend_ws
    uri = "ws://127.0.0.1:8001/ui"

    while True:
        try:
            async with websockets.connect(uri) as ws:
                async with backend_ws_lock:
                    backend_ws = ws
                debugger_state["console_output"].append("[Sistema] Connesso al Backend centrale.")
                await send_command_to_backend('update_breakpoints')

                while True:
                    message = await ws.recv()
                    payload = json.loads(message)

                    if payload.get("type") == "UPDATE_STATE" or payload.get("type") == "CONSOLE_LOG":
                        data = payload.get("data", payload)

                        if "pc" in data:
                            debugger_state["pc"] = data.get("pc", "0x10000")
                        if "registers" in data:
                            debugger_state["registers"] = data.get("registers", debugger_state["registers"])
                        if "memory" in data:
                            debugger_state["memory"] = data.get("memory", {})

                        if "console_output" in data and data["console_output"]:
                            if isinstance(data["console_output"], list):
                                debugger_state["console_output"].extend(data["console_output"])
                            else:
                                debugger_state["console_output"].append(data["console_output"])
                        elif "message" in payload:
                            debugger_state["console_output"].append(payload["message"])

                        pc_corrente = debugger_state["pc"]
                        debugger_state["active_regs"] = set()
                        for row in debugger_state["source_code"]:
                            if row["is_instruction"] and row["pc_addr"] == pc_corrente:
                                debugger_state["active_regs"] = analizza_registri_attivi(row["text"])
                                break

                        if data.get("status") == "terminated" or data.get("terminated"):
                            debugger_state["terminated"] = True
                        else:
                            try:
                                debugger_state["terminated"] = int(debugger_state["pc"], 16) == EXIT_SENTINEL
                            except ValueError:
                                pass

                        debugger_state["source_code"] = load_source_code()

                        if len(debugger_state["console_output"]) > 100:
                            debugger_state["console_output"] = debugger_state["console_output"][-100:]

        except Exception:
            await asyncio.sleep(2)

app.on_startup(lambda: background_tasks.create(maintain_backend_connection()))

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
            ui.button('Salva & Compila', on_click=lambda: background_tasks.create(
                send_command_to_backend('compile', editor_textarea.value))).props(
                'flat color=orange icon=build text-xs')

            with ui.row().classes('bg-slate-800 p-1 rounded-lg border border-slate-700 gap-1'):
                async def handle_continue():
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
                    await send_command_to_backend('compile', editor_textarea.value)
                    debugger_state["terminated"] = False
                    debugger_state["pc"] = "0x10000"
                    debugger_state["active_regs"] = set()
                    await send_command_to_backend('restart')

                btn_restart = ui.button('Restart', on_click=lambda: background_tasks.create(handle_restart())).props(
                    'flat color=cyan icon=restart_alt text-xs')

            pc_label = ui.label("PC: 0x10000").classes(
                'bg-slate-800 px-4 py-1.5 rounded-lg border border-cyan-500/30 text-cyan-400 font-mono text-sm')

    with ui.row().classes('w-full p-4 gap-4 items-stretch'):
        with ui.card().classes(
                'bg-slate-950 border border-slate-800 p-2 shadow-xl flex-[2] min-w-[550px] h-[580px] flex flex-col'):
            with ui.tabs().classes('w-full border-b border-slate-800') as tabs:
                tab_debug = ui.tab('🔍 Monitor Debugger')
                tab_editor = ui.tab('📝 Scrivi Codice (src/main.s)')

            with ui.tab_panels(tabs, value=tab_debug).classes('w-full bg-transparent flex-grow p-2 text-xs'):
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

                with ui.tab_panel(tab_editor).classes('p-0 h-full flex flex-col'):
                    ui.label(
                        "Modifica direttamente il codice Assembly ARMv7 qui sotto, poi premi 'Salva & Compila':").classes(
                        'text-slate-400 mb-2 italic')
                    editor_textarea = ui.textarea(value=debugger_state["raw_source_text"]).classes(
                        'w-full flex-grow font-mono bg-slate-900 text-slate-100 p-4 rounded border border-slate-700 focus:border-cyan-500 resize-none h-[440px]'
                    ).props('square outlined dark')

        with ui.column().classes('flex-1 min-w-[350px] gap-4'):
            with ui.card().classes('bg-slate-800 border border-slate-700/50 p-4 shadow-xl w-full'):
                ui.label('Registri della CPU').classes('text-sm font-bold uppercase tracking-wider text-slate-400 mb-2')

                reg_containers = {}
                reg_labels = {}
                with ui.grid(columns=3).classes('w-full gap-2 text-sm font-mono'):
                    target_regs = [f"r{i}" for i in range(13)] + ["sp", "lr", "pc", "cpsr"]
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

    with ui.card().classes('w-full mx-4 mb-4 bg-slate-950 border border-slate-800 p-4 rounded-xl shadow-inner'):
        ui.label('Console Log Eventi / Output Simulatore').classes(
            'text-xs font-bold uppercase tracking-wider text-green-500 mb-2')
        log_column_container = ui.column().classes('w-full gap-0.5 font-mono text-xs max-h-[120px] overflow-y-auto')

    last_src_version = list(debugger_state["source_code"])

    def update_ui_loop():
        global _last_rendered_pc, _last_rendered_memory, _last_rendered_logs_len, _last_rendered_breakpoints
        nonlocal last_src_version

        if debugger_state["source_code"] != last_src_version:
            render_debug_view()
            last_src_version = list(debugger_state["source_code"])

        current_pc_str = debugger_state['pc']
        pc_label.set_text(f"PC attuale: {current_pc_str}")

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
        else:
            btn_run.enable();
            btn_step.enable();
            btn_pause.enable();

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

        if len(debugger_state["console_output"]) != _last_rendered_logs_len:
            log_column_container.clear()
            with log_column_container:
                for msg in debugger_state["console_output"][-12:]:
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

    ui.timer(0.1, update_ui_loop)

ui.run(host='0.0.0.0', port=8080, title="ARM Custom Integrated Debugger", reload=False, show=True)