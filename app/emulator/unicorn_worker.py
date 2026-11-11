# app/emulator/unicorn_worker.py
# Worker dell'emulatore ARMv7 basato su Unicorn Engine.
# Si connette al backend FastAPI via WebSocket (/telemetry),
# riceve comandi di controllo (step, continue, pause, restart, set_speed)
# e invia lo stato dell'emulatore (registri, memoria, output) al backend.

import json
import logging
import struct
import sys
import time
from pathlib import Path

import websocket
from elftools.elf.elffile import ELFFile
from unicorn import *
from unicorn.arm_const import *

# Aggiunge la root del progetto al path per importare i percorsi condivisi
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from app.paths import BUILD_ELF

log = logging.getLogger("emulatore")

# Indirizzo base di caricamento del codice (deve coincidere con ARMCompiler.CODE_ADDRESS)
CODE_ADDRESS = 0x10000


PAGE = 0x1000


def _page_range(start, size):
    """Allinea una regione ai confini di pagina (4 KB), come richiesto da Unicorn."""
    first = start & ~(PAGE - 1)
    last = (start + size + PAGE - 1) & ~(PAGE - 1)
    return first, last


def load_program(mu, elf_path):
    """
    Carica nell'emulatore l'ELF prodotto dal linker GNU.

    Mappa e copia in memoria ogni segmento caricabile (PT_LOAD), azzerando la parte
    non presente nel file (.bss), e restituisce l'entry point (_start).
    Se il programma non è ancora stato compilato mappa una pagina vuota a
    CODE_ADDRESS, così l'interfaccia mostra comunque registri e stack.

    Parametri:
        mu       -- istanza Unicorn
        elf_path -- percorso dell'ELF (build/main.elf)

    Ritorna:
        (entry_point, caricato) -- caricato è False se l'ELF non esiste
    """
    if not elf_path.exists():
        mu.mem_map(CODE_ADDRESS, PAGE)
        return CODE_ADDRESS, False

    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        segments = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD" and s["p_memsz"] > 0]

        # Unisce le pagine dei segmenti: due segmenti possono condividere la stessa pagina
        regions = []
        for seg in sorted(segments, key=lambda s: s["p_vaddr"]):
            first, last = _page_range(seg["p_vaddr"], seg["p_memsz"])
            if regions and first <= regions[-1][1]:
                regions[-1][1] = max(regions[-1][1], last)
            else:
                regions.append([first, last])
        for first, last in regions:
            mu.mem_map(first, last - first)

        for seg in segments:
            data = seg.data()
            mu.mem_write(seg["p_vaddr"], data + b"\x00" * (seg["p_memsz"] - len(data)))

        return elf.header.e_entry, True


DATA_SECTIONS = (".data", ".bss")
MAX_DATA_WORDS = 64   # parole mostrate nel riquadro "Memoria .data" (256 byte)


def program_data_info(elf_path):
    """
    Legge dall'ELF le sezioni dati e le etichette che contengono.

    Ritorna (regioni, etichette): regioni è una lista di (nome, indirizzo, dimensione)
    per .data e .bss; etichette è {indirizzo: nome} per i simboli definiti in quelle
    sezioni (es. msg, val), esclusi i simboli interni dell'assembler come $d.
    """
    if not elf_path.exists():
        return [], {}
    regions, labels = [], {}
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        indexes = {}
        for i, sec in enumerate(elf.iter_sections()):
            if sec.name in DATA_SECTIONS and sec["sh_size"] > 0:
                regions.append((sec.name, sec["sh_addr"], sec["sh_size"]))
                indexes[i] = sec.name
        symtab = elf.get_section_by_name(".symtab")
        if symtab is not None:
            for sym in symtab.iter_symbols():
                if (sym.name and not sym.name.startswith("$") and sym["st_shndx"] in indexes
                        and sym["st_info"]["type"] in ("STT_NOTYPE", "STT_OBJECT")):
                    labels.setdefault(sym["st_value"], sym.name)
    return regions, labels


def data_memory_view(mu, regions, labels):
    """
    Righe del riquadro "Memoria .data": una per parola di 4 byte, con indirizzo,
    etichetta (se una inizia in quella parola), valore esadecimale e caratteri ASCII.
    """
    rows = []
    for name, start, size in regions:
        addr = start & ~0x3
        while addr < start + size and len(rows) < MAX_DATA_WORDS:
            try:
                raw = bytes(mu.mem_read(addr, 4))
            except UcError:
                break
            names = [labels[a] for a in range(addr, addr + 4) if a in labels]
            rows.append({
                "addr": hex(addr),
                "section": name,
                "label": ", ".join(names),
                "value": f"0x{struct.unpack('<I', raw)[0]:08X}",
                "ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in raw),
            })
            addr += 4
    return rows


def send_state_to_dashboard(mu, ws, custom_output=None, is_terminated=False, data_memory=None):
    """
    Legge lo stato corrente dell'emulatore (registri + dump dello stack)
    e lo invia al backend come payload JSON.

    Il dump della memoria copre 11 word intorno allo Stack Pointer
    (offset da -20 a +20 byte rispetto a SP allineato a 4 byte).

    Parametri:
        mu             -- istanza Unicorn
        ws             -- connessione WebSocket verso il backend
        custom_output  -- messaggio testuale opzionale da aggiungere alla console
        is_terminated  -- True se il programma ha raggiunto la condizione di terminazione
    """
    try:
        # Legge i registri r0-r12
        regs = {}
        for i in range(13):
            reg_const = globals().get(f"UC_ARM_REG_R{i}")
            if reg_const is not None:
                regs[f"r{i}"] = hex(mu.reg_read(reg_const))
        # Aggiunge i registri speciali
        regs.update({
            "sp": hex(mu.reg_read(UC_ARM_REG_SP)),
            "lr": hex(mu.reg_read(UC_ARM_REG_LR)),
            "pc": hex(mu.reg_read(UC_ARM_REG_PC)),
            "cpsr": hex(mu.reg_read(UC_ARM_REG_CPSR))
        })

        # Costruisce il dump della memoria intorno allo Stack Pointer
        sp_val = mu.reg_read(UC_ARM_REG_SP)
        sp_aligned = sp_val & ~0x3  # Allinea SP al multiplo di 4 più vicino
        memory_dump = {}

        for offset in range(-20, 24, 4):
            addr = sp_aligned + offset
            try:
                data_bytes = mu.mem_read(addr, 4)
                # Interpreta i 4 byte come intero little-endian senza segno
                val = struct.unpack("<I", data_bytes)[0]
                memory_dump[hex(addr)] = f"0x{val:08X}"
            except UcError:
                pass  # L'indirizzo potrebbe essere fuori dalla memoria mappata

        payload = {
            "pc": regs["pc"],
            "registers": regs,
            "memory": memory_dump,
            "data_memory": data_memory or [],
            "status": "terminated" if is_terminated else "running",
            # Un messaggio (stringa), più messaggi (lista) oppure None
            "console_output": custom_output
        }
        ws.send(json.dumps(payload))
    except Exception as e:
        log.warning("Invio dello stato al backend fallito: %s", e)


# Stack: 64 KB a partire da 0x70000000; SP parte dalla word più alta (full-descending)
STACK_ADDR, STACK_SIZE = 0x70000000, 0x10000

# Chiamate di sistema Linux ARM (EABI): numero in r7, argomenti in r0-r2, risultato in r0
SYS_EXIT, SYS_WRITE, SYS_EXIT_GROUP = 1, 4, 248
EBADF, ENOSYS = 9, 38

# Esecuzione continua: ogni "tick" legge i comandi, esegue un blocco di istruzioni
# e invia un solo aggiornamento all'interfaccia (al massimo 20 al secondo).
TICK = 0.05
MAX_BLOCK = 20_000          # istruzioni per tick alla velocità massima
DEFAULT_SPEED = 5           # istruzioni al secondo all'avvio (0 = massima)


def execute_single_step(mu):
    """
    Esegue esattamente una istruzione ARM partendo dal PC corrente.

    Parametri:
        mu -- istanza Unicorn
    """
    current_pc = mu.reg_read(UC_ARM_REG_PC)
    mu.emu_start(current_pc, 0xFFFFFFFF, timeout=0, count=1)


class Emulator:
    """
    Un programma caricato in Unicorn, con chiamate di sistema e breakpoint.

    run(n) esegue fino a n istruzioni e si ferma prima se il programma chiama
    exit, se raggiunge un breakpoint (prima di eseguire l'istruzione) o se la CPU
    solleva un'eccezione. I messaggi per la console si accumulano in
    self.messages e vengono inviati dal ciclo principale insieme allo stato.
    """

    def __init__(self, elf_path):
        self.mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        entry_point, self.loaded = load_program(self.mu, elf_path)
        self.data_regions, self.data_labels = program_data_info(elf_path)
        self.mu.mem_map(STACK_ADDR, STACK_SIZE)
        self.mu.reg_write(UC_ARM_REG_SP, STACK_ADDR + STACK_SIZE - 4)
        self.mu.reg_write(UC_ARM_REG_PC, entry_point)

        self.breakpoints = set()   # indirizzi interi
        self.terminated = False    # exit chiamata o eccezione della CPU
        self.exit_code = None
        self.messages = []
        self._first_instruction = False
        self._hit_breakpoint = None

        self.mu.hook_add(UC_HOOK_INTR, self._on_interrupt)
        self.mu.hook_add(UC_HOOK_CODE, self._on_code)

    @property
    def pc(self):
        return self.mu.reg_read(UC_ARM_REG_PC)

    def set_breakpoints(self, addresses):
        """Accetta indirizzi come stringhe esadecimali ('0x10004') o interi."""
        result = set()
        for a in addresses:
            try:
                result.add(int(a, 16) if isinstance(a, str) else int(a))
            except ValueError:
                pass
        self.breakpoints = result

    # --- hook di Unicorn ---------------------------------------------------

    def _on_code(self, uc, address, size, user_data):
        # La prima istruzione di ogni run() viene sempre eseguita: si può ripartire
        # da un breakpoint senza fermarsi subito sullo stesso indirizzo.
        if self._first_instruction:
            self._first_instruction = False
            return
        if address in self.breakpoints:
            self._hit_breakpoint = address
            uc.emu_stop()   # si ferma prima di eseguire l'istruzione

    def _on_interrupt(self, uc, intno, user_data):
        if intno != 2:      # 2 = SVC (chiamata di sistema)
            return
        r7 = uc.reg_read(UC_ARM_REG_R7)
        r0 = uc.reg_read(UC_ARM_REG_R0)

        if r7 in (SYS_EXIT, SYS_EXIT_GROUP):
            code = r0 if r0 < 0x80000000 else r0 - 0x100000000   # r0 con segno
            self.exit_code = r0
            self.terminated = True
            self.messages.append(f"[Fine Esecuzione] Il programma ha chiamato exit({code}).")
            uc.emu_stop()

        elif r7 == SYS_WRITE:
            buf, length = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)
            if r0 not in (1, 2):
                self.messages.append(f"[Avviso] write sul file descriptor {r0}: sono supportati solo 1 (stdout) e 2 (stderr).")
                uc.reg_write(UC_ARM_REG_R0, (-EBADF) & 0xFFFFFFFF)
                return
            try:
                data = bytes(uc.mem_read(buf, length))
            except UcError:
                self.messages.append(f"[Avviso] write: il buffer {hex(buf)} non è in memoria.")
                uc.reg_write(UC_ARM_REG_R0, (-14) & 0xFFFFFFFF)   # -EFAULT
                return
            text = data.decode("utf-8", errors="replace")
            # Ogni messaggio è già una riga della console: si toglie solo l'a capo finale
            if text.endswith("\n"):
                text = text[:-1]
            label = "[Output Emulatore]" if r0 == 1 else "[Output Emulatore stderr]"
            self.messages.append(f"{label} {text}")
            uc.reg_write(UC_ARM_REG_R0, length)   # come Linux: numero di byte scritti

        else:
            self.messages.append(f"[Avviso] Chiamata di sistema {r7} non supportata: ignorata (r0 = -ENOSYS).")
            uc.reg_write(UC_ARM_REG_R0, (-ENOSYS) & 0xFFFFFFFF)

    # --- esecuzione ----------------------------------------------------------

    def run(self, count):
        """
        Esegue fino a count istruzioni.

        Ritorna il motivo della fermata: 'count', 'exit', 'breakpoint' o 'error'.
        """
        if self.terminated:
            return "exit"
        self._first_instruction = True
        self._hit_breakpoint = None
        try:
            self.mu.emu_start(self.pc, 0xFFFFFFFF, timeout=0, count=count)
        except UcError as e:
            self.terminated = True
            self.messages.append(f"[Fine Esecuzione] Eccezione della CPU a {hex(self.pc)}: {e}")
            return "error"
        if self.terminated:
            return "exit"
        if self._hit_breakpoint is not None:
            self.messages.append(f"[Breakpoint] Raggiunto indirizzo {hex(self._hit_breakpoint)}")
            return "breakpoint"
        return "count"

    def data_view(self):
        return data_memory_view(self.mu, self.data_regions, self.data_labels)

    def take_messages(self):
        msgs, self.messages = self.messages, []
        return msgs or None


def main():
    """
    Ciclo principale del worker.

    1. Carica build/main.elf (o una pagina vuota se non è ancora stato compilato).
    2. Si connette al backend via WebSocket (ws://127.0.0.1:8001/telemetry).
    3. A ogni tick (50 ms) legge i comandi; se l'esecuzione continua è attiva
       esegue un blocco di istruzioni in base alla velocità scelta e invia un
       solo aggiornamento dello stato.
    """
    elf_path = BUILD_ELF
    not_compiled = "[Unicorn] Nessun programma compilato: correggi il codice e premi 'Salva & Compila'."

    print("[Unicorn] Connessione al server centrale...")
    try:
        ws = websocket.create_connection("ws://127.0.0.1:8001/telemetry")
        ws.settimeout(TICK)  # il timeout di ricezione fa da ritmo al ciclo
        print("[Unicorn] Connessione stabilita con successo.")
    except Exception as e:
        log.error("Impossibile collegarsi al backend su ws://127.0.0.1:8001/telemetry: %s", e)
        return

    emu = Emulator(elf_path)
    breakpoints = []
    speed = DEFAULT_SPEED       # istruzioni al secondo; 0 = massima
    is_running = False
    credit, last_tick = 0.0, time.monotonic()

    def send(extra=None):
        msgs = emu.take_messages() or []
        if extra:
            msgs.insert(0, extra)
        send_state_to_dashboard(emu.mu, ws, custom_output=msgs or None, is_terminated=emu.terminated,
                                data_memory=emu.data_view())

    send("[Unicorn] Stato iniziale caricato e pronto." if emu.loaded else not_compiled)

    while True:
        # --- Fase 1: comandi dal backend ---
        try:
            cmd_data = json.loads(ws.recv())
            if cmd_data.get("type") == "COMMAND":
                action = cmd_data.get("action")
                if "breakpoints" in cmd_data:
                    breakpoints = cmd_data["breakpoints"]
                    emu.set_breakpoints(breakpoints)
                if "speed" in cmd_data:
                    try:
                        speed = max(0, int(cmd_data["speed"]))
                    except (TypeError, ValueError):
                        pass

                if action == "restart":
                    is_running = False
                    emu = Emulator(elf_path)
                    emu.set_breakpoints(breakpoints)
                    send("[Restart] Emulatore resettato." if emu.loaded else not_compiled)

                elif emu.terminated and action in ("step", "continue"):
                    # Programma finito: si risponde con lo stato finale, senza eseguire altro
                    send("[Sistema] Il programma è terminato: premi Restart per ricominciare.")

                elif action == "step":
                    is_running = False
                    emu.run(1)
                    send()

                elif action == "continue":
                    is_running = True
                    credit, last_tick = 1.0, time.monotonic()   # la prima istruzione parte subito

                elif action == "pause":
                    if is_running:
                        is_running = False
                        send("[Sistema] Esecuzione in pausa.")

        except websocket.WebSocketTimeoutException:
            pass  # nessun comando: si prosegue con l'esecuzione continua
        except (OSError, getattr(websocket, "WebSocketException", OSError)) as e:
            log.info("Connessione con il backend chiusa (%s): l'emulatore si ferma", e)
            break
        except Exception:
            # Un errore inatteso nella gestione di un comando: lo si registra con il
            # traceback invece di nasconderlo, poi l'emulatore si ferma
            log.exception("Errore inatteso nel ciclo dell'emulatore")
            break

        # --- Fase 2: esecuzione continua, un blocco per tick ---
        if is_running and not emu.terminated:
            now = time.monotonic()
            if speed == 0:
                budget = MAX_BLOCK
            else:
                credit = min(credit + speed * (now - last_tick), speed + 1.0)
                budget = int(credit)
                credit -= budget
            last_tick = now

            if budget > 0:
                reason = emu.run(budget)
                if reason != "count":
                    is_running = False
                send()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main()
