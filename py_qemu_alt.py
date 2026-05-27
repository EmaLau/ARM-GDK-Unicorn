import struct
import time
import websocket
import json
import os  # <-- Aggiunto per il controllo dell'esistenza del file ELF
from unicorn import *
from unicorn.arm_const import *
from elftools.elf.elffile import ELFFile

breakpoints = set()
EXIT_SENTINEL = 0xFFFF0000


def load_elf_to_unicorn(mu, filename):
    with open(filename, 'rb') as f:
        elf = ELFFile(f)
        for segment in elf.iter_segments():
            if segment['p_type'] == 'PT_LOAD':
                size = segment['p_memsz']
                addr = segment['p_vaddr']
                data = segment.data()
                if size == 0:
                    continue
                aligned_size = (size + 0xfff) & ~0xfff
                aligned_addr = addr & ~0xfff
                try:
                    mu.mem_map(aligned_addr, aligned_size)
                except UcError:
                    pass
                if data:
                    mu.mem_write(addr, data)
        return elf.header['e_entry']


def send_state_to_dashboard(mu, ws, custom_output=None, is_terminated=False):
    try:
        regs = {}
        for i in range(13):
            reg_const = globals().get(f"UC_ARM_REG_R{i}")
            if reg_const is not None:
                regs[f"r{i}"] = hex(mu.reg_read(reg_const))
        regs.update({
            "sp": hex(mu.reg_read(UC_ARM_REG_SP)),
            "lr": hex(mu.reg_read(UC_ARM_REG_LR)),
            "pc": hex(mu.reg_read(UC_ARM_REG_PC)),
            "cpsr": hex(mu.reg_read(UC_ARM_REG_CPSR))
        })

        # Dump dello Stack allineato a word di 4 byte
        sp_val = mu.reg_read(UC_ARM_REG_SP)
        # Allineamento dell'indirizzo base dello stack a 4 byte
        sp_aligned = sp_val & ~0x3
        memory_dump = {}

        # Mostra 10 locazioni di memoria (4 byte ciascuna) intorno allo Stack Pointer
        for offset in range(-20, 24, 4):
            addr = sp_aligned + offset
            try:
                data_bytes = mu.mem_read(addr, 4)
                val = struct.unpack("<I", data_bytes)[0]
                memory_dump[hex(addr)] = f"0x{val:08X}"
            except UcError:
                pass  # Evita blocchi se lo stack tocca pagine non mappate

        payload = {
            "pc": regs["pc"],
            "registers": regs,
            "memory": memory_dump,
            "status": "terminated" if is_terminated else "running",
            "console_output": custom_output
        }

        ws.send(json.dumps(payload))
    except Exception as e:
        print(f"[Unicorn] Errore nell'invio dello stato: {e}")


def execute_single_step(mu):
    current_pc = mu.reg_read(UC_ARM_REG_PC)
    mu.emu_start(current_pc, current_pc + 4, timeout=0, count=1)


def build_emulator(ws):
    mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    entry_point = load_elf_to_unicorn(mu, "build/main.elf")
    mu.reg_write(UC_ARM_REG_PC, entry_point)
    mu.reg_write(UC_ARM_REG_SP, 0x7FFFF000)

    def hook_intr(uc, intno, user_data):
        if intno != 2:
            return
        r7 = uc.reg_read(UC_ARM_REG_R7)
        r0 = uc.reg_read(UC_ARM_REG_R0)
        if r7 == 1:
            msg = f"[Unicorn] Syscall exit({r0}) intercettata. Termino l'esecuzione."
            print(msg)
            send_state_to_dashboard(uc, ws, custom_output=msg, is_terminated=True)
            uc.emu_stop()
        elif r7 == 4:
            r1 = uc.reg_read(UC_ARM_REG_R1)
            r2 = uc.reg_read(UC_ARM_REG_R2)
            try:
                buf = uc.mem_read(r1, r2)
                decoded_str = buf.decode('utf-8', errors='ignore').strip()
                msg = f"[Output Emulatore]: {decoded_str}"
                print(msg)
                send_state_to_dashboard(uc, ws, custom_output=msg)
            except UcError:
                pass

    mu.hook_add(UC_HOOK_INTR, hook_intr)
    return mu


def main():
    print("[Unicorn] Connessione al server centrale...")
    ws = websocket.create_connection("ws://localhost:8001/telemetry")
    ws.settimeout(0.05)
    print("[Unicorn] Connessione stabilita con successo.")

    # --- CICLO DI ATTESA RESILIENTE PER LA COMPILAZIONE DALLA UI ---
    elf_path = "build/main.elf"
    while not os.path.exists(elf_path):
        print(f"⏳ [Unicorn] '{elf_path}' non trovato. In attesa del click su 'Compila' nella UI...")
        time.sleep(2)

    print("🚀 [Unicorn] Rilevato 'main.elf'! Inizializzazione dell'emulatore...")
    # ---------------------------------------------------------------

    mu = build_emulator(ws)
    is_running = False
    has_terminated = False

    send_state_to_dashboard(mu, ws, custom_output="[Unicorn] Stato iniziale caricato e pronto.")

    while True:
        try:
            msg = ws.recv()
            cmd_data = json.loads(msg)
            if cmd_data.get("type") == "COMMAND":
                action = cmd_data.get("action")

                # Sincronizza i breakpoint aggiornati dalla UI
                if "breakpoints" in cmd_data:
                    global breakpoints
                    breakpoints = set(cmd_data["breakpoints"])

                print(f"[Unicorn] Ricevuto comando dalla UI: {action}")

                if action == "restart":
                    is_running = False
                    has_terminated = False
                    try:
                        # Controlla se l'ELF è ancora presente prima del restart
                        if os.path.exists(elf_path):
                            mu = build_emulator(ws)
                            send_state_to_dashboard(mu, ws, custom_output="[Restart] Emulatore resettato.",
                                                    is_terminated=False)
                        else:
                            print("[Unicorn] Errore: 'main.elf' sparito durante il restart.")
                    except Exception as e:
                        print(f"[Unicorn] Errore restart: {e}")
                    continue

                if has_terminated:
                    send_state_to_dashboard(mu, ws, is_terminated=True)
                    continue

                if action == "step":
                    is_running = False
                    try:
                        execute_single_step(mu)
                        send_state_to_dashboard(mu, ws)
                    except UcError:
                        has_terminated = True
                        send_state_to_dashboard(mu, ws, custom_output="[Fine Esecuzione] Eccezione CPU rilevata.",
                                                is_terminated=True)
                elif action == "continue":
                    is_running = True
                elif action == "pause":
                    is_running = False
                    send_state_to_dashboard(mu, ws, custom_output="[Sistema] Esecuzione in pausa.")

        except websocket.WebSocketTimeoutException:
            pass
        except (websocket.WebSocketConnectionClosedException, ConnectionResetError):
            print("[Unicorn] Connessione alla Dashboard persa. Uscita.")
            break
        except Exception as e:
            print(f"[Unicorn] Errore imprevisto: {e}")
            break

        if is_running and not has_terminated:
            try:
                execute_single_step(mu)
                current_pc = mu.reg_read(UC_ARM_REG_PC)

                if current_pc in breakpoints:
                    is_running = False
                    send_state_to_dashboard(mu, ws, custom_output=f"[Breakpoint] Raggiunto indirizzo {hex(current_pc)}")
                elif current_pc == EXIT_SENTINEL:
                    is_running = False
                    has_terminated = True
                    send_state_to_dashboard(mu, ws,
                                            custom_output="[Fine Esecuzione] Raggiunta istruzione di terminazione.",
                                            is_terminated=True)
                else:
                    send_state_to_dashboard(mu, ws)

            except UcError:
                is_running = False
                has_terminated = True
                send_state_to_dashboard(mu, ws, custom_output="[Fine Esecuzione] Programma terminato.",
                                        is_terminated=True)

            time.sleep(0.15)


if __name__ == "__main__":
    main()