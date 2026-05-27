import re
from pathlib import Path
from keystone import Ks, KS_ARCH_ARM, KS_MODE_ARM, KS_MODE_V8
import unicorn
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R7, UC_ARM_REG_PC, UC_ARM_REG_SP
)


class CustomARMDebugger:
    def __init__(self, src_path: Path):
        self.src_path = src_path
        self.mu = None
        self.encoding = []

        # Mappatura degli indirizzi di memoria simulati
        self.CODE_ADDRESS = 0x1000  # Area .text
        self.DATA_ADDRESS = 0x2000  # Area .data
        self.STACK_ADDRESS = 0x70000000  # Area Stack

        # Stato dell'output della console emulata
        self.console_output = ""

    def pre_process_and_compile(self):
        """
        Legge il file main.s, mappa le label di testo ed estrae
        le istruzioni pure tradotte in formato ARMv7 nativo per Keystone.
        """
        if not self.src_path.exists():
            raise FileNotFoundError(f"Impossibile trovare il file sorgente in {self.src_path}")

        with open(self.src_path, "r") as f:
            lines = f.readlines()

        instructions = []
        data_buffer = b""

        # 1. Primo passo: estraiamo il buffer dati per calcolare la lunghezza corretta
        for line in lines:
            line_clean = line.strip().split("@")[0].strip()
            if "hello:" in line_clean or ".ascii" in line_clean:
                if '"' in line:
                    string_content = line.split('"')[1].replace('\\n', '\n')
                    data_buffer = string_content.encode('utf-8')

        # 2. Secondo passo: processiamo le istruzioni
        for line in lines:
            line_clean = line.strip().split("@")[0].strip()  # Rimuove commenti

            if not line_clean or line_clean.startswith(".global") or line_clean.startswith("_start:"):
                continue
            if "hello:" in line_clean or ".ascii" in line_clean or "hello_len =" in line_clean:
                continue
            if line_clean.startswith(".text") or line_clean.startswith(".data"):
                continue

            # Sostituisce la costante di lunghezza
            if "#hello_len" in line_clean:
                line_clean = line_clean.replace("#hello_len", f"#{len(data_buffer)}")

            # --- FIX CRITICO: TRADUZIONE DELLA PSEUDO-ISTRUZIONE =hello ---
            # Invece di ldr r1, #0x2000 (illegale), generiamo movw e movt staccate
            if "=hello" in line_clean:
                # Estraiamo il registro dinamico (es. r1, r0, ecc.)
                registro = line_clean.split()[1].replace(",", "")

                # Dividiamo l'indirizzo 0x2000 in parte alta e bassa a 16 bit
                addr_bassi = self.DATA_ADDRESS & 0xFFFF
                addr_alti = (self.DATA_ADDRESS >> 16) & 0xFFFF

                # Generiamo le due istruzioni reali che simulano il caricamento dell'indirizzo
                instructions.append(f"movw {registro}, #{hex(addr_bassi)}")
                instructions.append(f"movt {registro}, #{hex(addr_alti)}")
                continue

            instructions.append(line_clean)

        # Compilazione sicura con Keystone (Modalità ARMv7 + v8 estesa)
        ks = Ks(KS_ARCH_ARM, KS_MODE_ARM + KS_MODE_V8)
        self.encoding, _ = ks.asm("\n".join(instructions))
        return data_buffer

    def _syscall_hook(self, uc, intno, user_data):
        """Hook interno per catturare l'istruzione 'svc 0' e leggere i registri."""
        if intno == 2:  # Corrisponde all'interruzione SVC in Unicorn ARM
            r7 = uc.reg_read(UC_ARM_REG_R7)

            if r7 == 4:  # Linux Syscall: WRITE
                r0 = uc.reg_read(UC_ARM_REG_R0)  # FD (STDOUT)
                r1 = uc.reg_read(UC_ARM_REG_R1)  # Buffer Address
                r2 = uc.reg_read(UC_ARM_REG_R2)  # Length

                # Legge la stringa direttamente dalla RAM simulata
                data = uc.mem_read(r1, r2).decode('utf-8')
                self.console_output += data

            elif r7 == 1:  # Linux Syscall: EXIT
                r0 = uc.reg_read(UC_ARM_REG_R0)
                self.console_output += f"\n[Processo terminato con codice {r0}]\n"
                uc.emu_stop()

    def init_vm(self, data_buffer: b""):
        """Inizializza la macchina virtuale Unicorn e scrive codice/dati in memoria."""
        self.mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)

        # Alloca la memoria (i blocchi devono essere allineati a 4KB)
        self.mu.mem_map(self.CODE_ADDRESS, 4 * 1024)
        self.mu.mem_map(self.DATA_ADDRESS, 4 * 1024)
        self.mu.mem_map(self.STACK_ADDRESS - 0x10000, 64 * 1024)  # Stack

        # Scrive codice e dati nei rispettivi segmenti
        self.mu.mem_write(self.CODE_ADDRESS, bytes(self.encoding))
        if data_buffer:
            self.mu.mem_write(self.DATA_ADDRESS, data_buffer)

        # Inizializza i registri base
        self.mu.reg_write(UC_ARM_REG_SP, self.STACK_ADDRESS)

        # Aggiunge l'intercettore per le System Call (Corretto l'uso del modulo unicorn)
        self.mu.hook_add(unicorn.UC_HOOK_INTR, self._syscall_hook)

    def get_registers(self):
        """Restituisce un dizionario con lo stato attuale dei registri per la UI."""
        if not self.mu:
            return {}
        return {
            "R0": self.mu.reg_read(UC_ARM_REG_R0),
            "R1": self.mu.reg_read(UC_ARM_REG_R1),
            "R2": self.mu.reg_read(UC_ARM_REG_R2),
            "R7": self.mu.reg_read(UC_ARM_REG_R7),
            "PC": self.mu.reg_read(UC_ARM_REG_PC),
            "SP": self.mu.reg_read(UC_ARM_REG_SP),
        }

    def step(self):
        """Esegue una singola istruzione (Step Over/Into)."""
        if not self.mu:
            data = self.pre_process_and_compile()
            self.init_vm(data)

        current_pc = self.mu.reg_read(UC_ARM_REG_PC)
        # Se il PC è a 0, significa che dobbiamo ancora partire dall'inizio
        if current_pc == 0:
            current_pc = self.CODE_ADDRESS

        try:
            # Esegue esattamente 1 istruzione (count=1)
            self.mu.emu_start(current_pc, current_pc + 4, count=1)
        except unicorn.UcError:  # Corretto il riferimento al modulo unicorn
            # Gestisce la terminazione o la fine del codice
            pass

        return self.get_registers(), self.console_output