# ARM-GDK-Unicorn

ARM-GDK-Unicorn è un ambiente didattico per lo sviluppo, l'esecuzione e il debug di programmi **ARMv7 Assembly**, realizzato interamente in Python.

Il progetto combina:

* **Toolchain GNU per ARM** (`arm-none-eabi-as` e `arm-none-eabi-ld`) per assemblare e collegare il codice, con la stessa sintassi usata nei corsi;
* **Unicorn Engine** per l'emulazione della CPU ARMv7;
* **FastAPI** come hub di comunicazione;
* **NiceGUI** per l'interfaccia grafica web.

L'obiettivo è fornire una piattaforma semplice e comprensibile per studenti, docenti e appassionati di architettura dei calcolatori che desiderano osservare il comportamento interno di un processore ARM durante l'esecuzione di un programma Assembly.

---

# Caratteristiche principali

* Compilazione Assembly ARMv7 direttamente dal browser.
* Emulazione ARMv7 tramite Unicorn Engine.
* Debug passo-passo (Step).
* Esecuzione continua (Run).
* Pausa dell'esecuzione.
* Restart del programma.
* Gestione breakpoint.
* Visualizzazione registri CPU.
* Visualizzazione Stack.
* Console di output.
* Editor Assembly integrato.
* Aggiornamento dello stato in tempo reale tramite WebSocket.

---

# Architettura

Il sistema è composto da tre moduli indipendenti:

1. **Frontend NiceGUI**
2. **Backend FastAPI**
3. **Worker Unicorn**

La comunicazione avviene esclusivamente tramite WebSocket.

```text
Frontend ↔ Backend ↔ Unicorn Worker
```

## Diagramma Mermaid

```mermaid
---
title: ARM-GDK-Unicorn — Architettura e flusso dati
---
flowchart TB

    USER(["👤 Utente"])

    subgraph BOOT ["⚙️ Avvio — start.sh / start.bat"]
        direction LR
        SH["Crea venv Python
pip install requirements.txt
Controlla la toolchain GNU
Lancia i 3 moduli in ordine"]
    end

    subgraph FE ["🖥️ Frontend NiceGUI — porta 8080"]
        FE_SRC["Viewer sorgente"]
        FE_REG["Registri CPU"]
        FE_MEM["Dump Stack"]
        FE_CON["Console"]
        FE_EDT["Editor Assembly"]
        FE_BTN["Step · Run · Pause · Restart · Compila"]
    end

    subgraph BE ["🔀 Backend FastAPI — porta 8001"]
        BE_UI["WebSocket /ui"]
        BE_TEL["WebSocket /telemetry"]
        BE_COMP["ARMCompiler"]
    end

    subgraph EMU ["🔧 Unicorn Worker"]
        EMU_LOAD["Caricamento binario"]
        EMU_LOOP["Loop di esecuzione"]
        EMU_STATE["Telemetria CPU"]
    end

    USER --> FE_BTN
    FE_BTN --> BE_UI
    BE_UI --> BE_COMP
    BE_UI --> BE_TEL
    BE_TEL --> EMU_LOOP
    EMU_LOOP --> EMU_STATE
    EMU_STATE --> BE_TEL
    BE_TEL --> FE_SRC
    BE_TEL --> FE_REG
    BE_TEL --> FE_MEM
    BE_TEL --> FE_CON
```

---

# Struttura del progetto

```text
ARM-GDK-Unicorn/
│
├── start.sh
├── start.bat
├── requirements.txt
├── LICENSE
│
├── examples/
│   └── hello.s            programma di esempio
│
├── app/
│   ├── paths.py           percorsi condivisi dai tre moduli
│   │
│   ├── backend/
│   │   ├── main.py
│   │   └── core_compiler.py
│   │
│   ├── emulator/
│   │   └── unicorn_worker.py
│   │
│   └── frontend/
│       └── run.py
│
├── workspace/             generata, ignorata da git
│   └── main.s             copia di lavoro modificata dall'editor
├── build/                 generata, ignorata da git
│   └── main.bin           binario prodotto dal compilatore
└── env/
```

Al primo avvio `workspace/main.s` viene creato copiando `examples/hello.s`: l'editor modifica solo la copia di lavoro, quindi l'esempio e il repository restano puliti.

---

# Componenti

## Frontend

Percorso:

```text
app/frontend/run.py
```

Responsabilità:

* Editor Assembly
* Visualizzazione registri
* Visualizzazione stack
* Gestione breakpoint
* Console di output
* Invio comandi al backend

Porta:

```text
http://localhost:8080
```

---

## Backend

Percorso:

```text
app/backend/main.py
```

Responsabilità:

* Gestione WebSocket
* Compilazione del codice
* Smistamento dei messaggi
* Coordinamento tra GUI ed emulatore
* Accetta connessioni WebSocket solo dai moduli locali: le connessioni aperte da pagine web (header `Origin` di un browser) vengono rifiutate

Endpoint:

```text
/ws/ui
/ws/telemetry
```

---

## Compilatore ARM

Percorso:

```text
app/backend/core_compiler.py
```

Basato sulla toolchain GNU per ARM, cercata in automatico (vedi `app/toolchain.py`):

1. `arm-none-eabi-as -g` assembla `workspace/main.s` in `build/main.o`;
2. `arm-none-eabi-ld` lo collega in `build/main.elf`, con `.text` a `0x10000`, `.data` subito dopo ed entry point `_start`;
3. dalle informazioni di debug (DWARF) ricava `build/lines.json`, la corrispondenza tra righe del sorgente e indirizzi usata dall'interfaccia per evidenziare la riga corrente e posizionare i breakpoint.

La sintassi è quella dell'assembler GNU: etichette (anche sulla stessa riga dell'istruzione), sezioni `.text` e `.data`, direttive come `.word`, `.byte`, `.ascii`, `.asciz`, `.space`, `.align`, `.equ`, costanti come `len = . - msg`, pseudo-istruzioni come `ldr r1, =msg` e commenti con `@`.

Gli errori arrivano nella console dell'interfaccia con il numero di riga, ad esempio `[Compiler Errore] Riga 4: bad instruction 'bogus r1'`.

---

## Emulatore

Percorso:

```text
app/emulator/unicorn_worker.py
```

Basato su:

* Unicorn Engine

Funzionalità:

* Esecuzione ARMv7
* Step-by-step execution
* Gestione breakpoint
* Hook syscall Linux

Syscall supportate:

| Syscall | Descrizione |
| ------- | ----------- |
| 1       | exit        |
| 4       | write       |

---

# Installazione

## Requisiti

* Python 3.10 o successivo, con pip
* La toolchain GNU per ARM: servono solo `arm-none-eabi-as` e `arm-none-eabi-ld`

| Sistema | Installazione della toolchain |
| ------- | ----------------------------- |
| Windows | [Arm GNU Toolchain](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads), versione *arm-none-eabi* per host Windows; durante l'installazione spunta *Add path to environment variable* |
| macOS   | `brew install arm-none-eabi-binutils` |
| Linux   | `sudo apt install binutils-arm-none-eabi` (Debian, Ubuntu) oppure il pacchetto equivalente della distribuzione |

Se la toolchain è installata in una cartella non standard, indica la sua cartella `bin` nella variabile d'ambiente `ARM_TOOLCHAIN_DIR`. Per controllare che venga trovata:

```bash
python -m app.toolchain
```

---

## Dipendenze

```bash
pip install -r requirements.txt
```

Pacchetti principali:

```text
fastapi
uvicorn
nicegui
websockets
unicorn
websocket-client
pyelftools
```

---

# Avvio

## Linux / macOS

```bash
chmod +x start.sh
./start.sh
```

## Windows

```cmd
start.bat
```

Lo script:

1. crea il virtual environment;
2. installa le dipendenze e controlla la toolchain GNU (se manca, spiega come installarla e si ferma);
3. avvia Backend;
4. avvia Frontend;
5. avvia Unicorn Worker.

---

# Test

```bash
python -m unittest discover -s tests -v
```

I test compilano ed eseguono nell'emulatore una serie di programmi (cicli, funzioni, stringhe, `.word`, costanti calcolate, errori con numero di riga). Se la toolchain GNU non è installata vengono saltati.

---

# Workflow

1. Scrivere Assembly ARM nell'editor.
2. Premere **Compila**.
3. Premere **Restart**.
4. Eseguire:

   * Step
   * Run
   * Pause
5. Analizzare:

   * registri;
   * stack;
   * output console.

---

# Caso d'uso didattico

ARM-GDK-Unicorn è pensato per:

* corsi di Architettura degli Elaboratori;
* Sistemi Operativi;
* Programmazione Assembly ARM;
* Reverse Engineering;
* studio delle syscall Linux ARM;
* laboratori universitari.

---

# Tecnologie utilizzate

| Tecnologia      | Ruolo                   |
| --------------- | ----------------------- |
| Python          | Linguaggio principale   |
| FastAPI         | Backend                 |
| NiceGUI         | Frontend                |
| WebSocket       | Comunicazione real-time |
| GNU binutils    | Assembler e linker ARM  |
| Unicorn Engine  | Emulatore ARM           |
| PyELFTools      | Lettura dell'ELF e delle informazioni di debug |

---

# Licenza

Il codice di questo progetto è distribuito con licenza [MIT](LICENSE).

Le librerie usate hanno licenze proprie: Unicorn Engine e le GNU binutils sono distribuite con licenza GPL, FastAPI, NiceGUI e le altre dipendenze con licenze permissive. Il repository non include il loro codice: le librerie Python vengono installate da `requirements.txt`, la toolchain GNU separatamente.
