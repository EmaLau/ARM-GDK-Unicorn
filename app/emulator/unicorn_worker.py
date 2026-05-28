# app/emulator/unicorn_worker.py
# Worker dell'emulatore ARMv7 basato su Unicorn Engine.
# Si connette al backend FastAPI via WebSocket (/telemetry),
# riceve comandi di controllo (step, continue, pause, restart)
# e invia lo stato dell'emulatore (registri, memoria, output) al backend.

import json
import os
import struct
import time

import websocket
from unicorn import *
from unicorn.arm_const import *

# Indirizzo sentinella: se il PC raggiunge questo valore il programma è terminato
EXIT_SENTINEL = 0xFFFF0000
# Indirizzo base di caricamento del codice (deve coincidere con ARMCompiler.CODE_ADDRESS)
CODE_ADDRESS = 0x10000


def load_binary_to_unicorn(mu, filename):
    """
    Carica il flat binary prodotto dal compilatore nello spazio di indirizzamento
    dell'emulatore Unicorn.

    Mappa 1 MB di memoria a partire da CODE_ADDRESS per evitare errori
    di accesso alla memoria durante l'esecuzione. Se la memoria è già
    mappata (es. dopo un restart) l'eccezione viene ignorata silenziosamente.

    Parametri:
        mu       -- istanza Unicorn
        filename -- percorso del file binario (main.elf)

    Ritorna:
        CODE_ADDRESS -- indirizzo di ingresso del programma
    """
    with open(filename, 'rb') as f:
        data = f.read()

    total_memory_to_map = 0x100000  # 1 MegaByte
    try:
        mu.mem_map(CODE_ADDRESS, total_memory_to_map)
    except UcError:
        pass  # La regione era già mappata (es. restart senza ricreare l'istanza)

    mu.mem_write(CODE_ADDRESS, data)
    return CODE_ADDRESS


def send_state_to_dashboard(mu, ws, custom_output=None, is_terminated=False):
    """
    Legge lo stato corrente dell'emulatore (registri + dump dello stack)
    e lo invia al backend come payload JSON.

    Il dump della memoria copre le 20 word intorno allo Stack Pointer
    (offset da -80 a +92 byte rispetto a SP allineato a 4 byte).

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
            "status": "terminated" if is_terminated else "running",
            "console_output": custom_output
        }
        ws.send(json.dumps(payload))
    except Exception as e:
        print(f"[Unicorn] Errore invio telemetria: {e}")


def execute_single_step(mu):
    """
    Esegue esattamente una istruzione ARM (4 byte) partendo dal PC corrente.

    Parametri:
        mu -- istanza Unicorn
    """
    current_pc = mu.reg_read(UC_ARM_REG_PC)
    mu.emu_start(current_pc, current_pc + 4, timeout=0, count=1)


def build_emulator(ws, elf_path):
    """
    Crea e configura una nuova istanza dell'emulatore Unicorn ARMv7.

    Operazioni:
      1. Crea l'istanza Unicorn in modalità ARM a 32 bit
      2. Carica il flat binary in memoria
      3. Mappa e inizializza lo stack (64 KB a partire da 0x70000000)
      4. Imposta SP alla cima dello stack e PC all'entry point
      5. Registra l'hook per le syscall SVC (interrupt software)

    Syscall supportate (valore in r7):
      1 (exit)  -- termina l'emulazione e notifica la UI
      4 (write) -- legge la stringa da memoria e la invia come output

    Parametri:
        ws       -- connessione WebSocket per l'invio della telemetria
        elf_path -- percorso del flat binary da caricare

    Ritorna:
        mu -- istanza Unicorn configurata e pronta per l'esecuzione
    """
    mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    entry_point = load_binary_to_unicorn(mu, elf_path)

    # Alloca lo stack: 64 KB a partire dall'indirizzo 0x70000000
    STACK_ADDR, STACK_SIZE = 0x70000000, 0x10000
    try:
        mu.mem_map(STACK_ADDR, STACK_SIZE)
    except UcError:
        pass  # Già mappato in caso di restart
    # SP punta alla word più alta dello stack (convenzione ARM full-descending)
    mu.reg_write(UC_ARM_REG_SP, STACK_ADDR + STACK_SIZE - 4)
    mu.reg_write(UC_ARM_REG_PC, entry_point)

    def hook_intr(uc, intno, user_data):
        """
        Hook chiamato da Unicorn ad ogni interrupt software (SVC).
        Intercetta solo le syscall Linux ARMv7 (intno == 2).
        """
        if intno != 2:
            return  # Ignora altri tipi di interrupt

        r7 = uc.reg_read(UC_ARM_REG_R7)  # Numero della syscall
        r0 = uc.reg_read(UC_ARM_REG_R0)  # Primo argomento

        if r7 == 1:
            # Syscall exit(r0): comunica la terminazione alla UI e ferma l'emulatore
            msg = f"[Output Emulatore] Syscall exit({r0}) intercettata."
            send_state_to_dashboard(uc, ws, custom_output=msg, is_terminated=True)
            uc.emu_stop()

        elif r7 == 4:
            # Syscall write(r0=fd, r1=buf, r2=len): legge la stringa dal buffer e la stampa
            r1 = uc.reg_read(UC_ARM_REG_R1)  # Puntatore al buffer
            r2 = uc.reg_read(UC_ARM_REG_R2)  # Numero di byte da leggere
            try:
                buf = uc.mem_read(r1, r2)
                decoded_str = buf.decode('utf-8', errors='ignore').strip()
                msg = f"[Output Emulatore]: {decoded_str}"
                send_state_to_dashboard(uc, ws, custom_output=msg)
            except UcError:
                pass  # Il buffer punta a memoria non mappata

    mu.hook_add(UC_HOOK_INTR, hook_intr)
    return mu


def main():
    """
    Ciclo principale del worker.

    1. Verifica o crea un binario placeholder (NOP) se main.elf non esiste.
    2. Si connette al backend via WebSocket (ws://127.0.0.1:8001/telemetry).
    3. Entra in un loop che:
       a. Legge eventuali comandi dal backend (con timeout di 50ms)
       b. Se is_running, esegue un singolo step, aggiorna breakpoint
          e invia la telemetria al backend.
       c. Introduce un delay di 150ms tra i passi per permettere alla UI di aggiornare.
    """
    elf_path = "main.elf"
    breakpoints = set()

    # Crea un binario placeholder (MOV r0, r0 = NOP) se il file non esiste ancora
    if not os.path.exists(elf_path):
        with open(elf_path, "wb") as f:
            f.write(b"\x00\x00\xa0\xe3")  # Encoding ARM di: mov r0, #0

    print("[Unicorn] Connessione al server centrale...")
    try:
        ws = websocket.create_connection("ws://127.0.0.1:8001/telemetry")
        ws.settimeout(0.05)  # Timeout breve per non bloccare il loop principale
        print("[Unicorn] Connessione stabilita con successo.")
    except Exception as e:
        print(f"Errore connessione: {e}")
        return

    mu = build_emulator(ws, elf_path)
    is_running = False    # True quando l'esecuzione continua è attiva
    has_terminated = False  # True dopo aver raggiunto il punto di terminazione

    # Invia lo stato iniziale così la UI mostra i valori dei registri prima di qualsiasi step
    send_state_to_dashboard(mu, ws, custom_output="[Unicorn] Stato iniziale caricato e pronto.")

    while True:
        # --- Fase 1: ricezione dei comandi dal backend ---
        try:
            msg = ws.recv()
            cmd_data = json.loads(msg)
            if cmd_data.get("type") == "COMMAND":
                action = cmd_data.get("action")

                # Aggiorna sempre i breakpoint se presenti nel payload
                if "breakpoints" in cmd_data:
                    breakpoints = set(cmd_data["breakpoints"])

                if action == "restart":
                    # Reimposta lo stato e ricrea l'emulatore da zero
                    is_running, has_terminated = False, False
                    mu = build_emulator(ws, elf_path)
                    send_state_to_dashboard(mu, ws, custom_output="[Restart] Emulatore resettato.")
                    continue

                # Se il programma è già terminato, risponde comunque con lo stato finale
                if has_terminated:
                    send_state_to_dashboard(mu, ws, is_terminated=True)
                    continue

                if action == "step":
                    # Esegue un singolo passo e ferma l'esecuzione continua
                    is_running = False
                    try:
                        execute_single_step(mu)
                        send_state_to_dashboard(mu, ws)
                    except UcError:
                        has_terminated = True
                        send_state_to_dashboard(mu, ws, custom_output="[Fine Esecuzione] Eccezione CPU.",
                                                is_terminated=True)

                elif action == "continue":
                    is_running = True  # Avvia l'esecuzione continua

                elif action == "pause":
                    is_running = False
                    send_state_to_dashboard(mu, ws, custom_output="[Sistema] Esecuzione in pausa.")

        except websocket.WebSocketTimeoutException:
            pass  # Nessun comando disponibile: prosegue con l'esecuzione continua
        except Exception:
            break  # Errore di connessione: termina il worker

        # --- Fase 2: esecuzione continua (un passo per iterazione) ---
        if is_running and not has_terminated:
            try:
                execute_single_step(mu)
                current_pc = mu.reg_read(UC_ARM_REG_PC)
                current_pc_hex = hex(current_pc)

                if current_pc_hex in breakpoints:
                    # Breakpoint raggiunto: sospende l'esecuzione e notifica la UI
                    is_running = False
                    send_state_to_dashboard(mu, ws, custom_output=f"[Breakpoint] Raggiunto indirizzo {current_pc_hex}")

                elif current_pc == EXIT_SENTINEL:
                    # Istruzione sentinella di fine programma
                    is_running, has_terminated = False, True
                    send_state_to_dashboard(mu, ws,
                                            custom_output="[Fine Esecuzione] Raggiunta istruzione di terminazione.",
                                            is_terminated=True)
                else:
                    # Passo normale: invia la telemetria aggiornata
                    send_state_to_dashboard(mu, ws)

            except UcError:
                # Eccezione CPU (accesso a memoria non valida, istruzione illegale, ecc.)
                is_running, has_terminated = False, True
                send_state_to_dashboard(mu, ws, custom_output="[Fine Esecuzione] Programma terminato.",
                                        is_terminated=True)

            # Rallenta l'esecuzione continua per permettere aggiornamenti fluidi della UI
            time.sleep(0.15)


if __name__ == "__main__":
    main()
