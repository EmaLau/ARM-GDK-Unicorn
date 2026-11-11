@ massimo.s — Scansione di un array in memoria: trova il valore massimo.
@ Mostra .word, l'indirizzamento con offset e post-incremento, e i salti
@ condizionati. Il massimo (42) è il codice di uscita. Nel riquadro
@ "Memoria .data" si vede l'array con la sua etichetta.

.text
.global _start

_start:
    ldr r1, =numeri         @ r1 = indirizzo del primo elemento
    ldr r2, =quanti
    ldr r2, [r2]            @ r2 = numero di elementi
    ldr r0, [r1], #4        @ r0 = massimo provvisorio = numeri[0]; r1 avanza di 4
    subs r2, r2, #1         @ un elemento è già stato letto

ciclo:
    beq fine                @ nessun elemento rimasto
    ldr r3, [r1], #4        @ r3 = elemento corrente; r1 avanza di 4
    cmp r3, r0
    movgt r0, r3            @ se r3 > r0 allora r0 = r3 (istruzione condizionata)
    subs r2, r2, #1
    b ciclo

fine:
    mov r7, #1              @ sys_exit(r0)
    svc 0

.data
numeri: .word 7, 19, 3, 42, 15, 8
quanti: .word 6
