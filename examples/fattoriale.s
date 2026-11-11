@ fattoriale.s — Funzione ricorsiva con bl, push e pop.
@ Calcola 5! = 120 e lo restituisce come codice di uscita.
@ Con Step si vede lo stack crescere a ogni chiamata (riquadro "Stack")
@ e il registro lr che conserva l'indirizzo di ritorno.

.text
.global _start

_start:
    mov r0, #5              @ argomento: n = 5
    bl fattoriale           @ r0 = fattoriale(5)
    mov r7, #1              @ sys_exit(r0)
    svc 0

@ fattoriale(n): r0 = n all'ingresso, r0 = n! all'uscita
fattoriale:
    push {r4, lr}           @ salva r4 e l'indirizzo di ritorno sullo stack
    mov r4, r0              @ r4 = n
    cmp r0, #1
    movle r0, #1            @ caso base: n <= 1 -> 1
    ble esci
    sub r0, r0, #1
    bl fattoriale           @ r0 = fattoriale(n - 1)
    mul r0, r4, r0          @ r0 = n * fattoriale(n - 1)
esci:
    pop {r4, pc}            @ ripristina r4 e torna al chiamante
