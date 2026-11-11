@ lunghezza.s — Lunghezza di una stringa terminata da zero (come strlen in C),
@ poi stampa la stringa con la chiamata di sistema write usando la lunghezza
@ calcolata. Mostra .asciz, ldrb (lettura di un byte) e le chiamate di sistema.

.text
.global _start

_start:
    ldr r1, =messaggio      @ r1 = indirizzo della stringa
    mov r2, #0              @ r2 = lunghezza

conta:
    ldrb r3, [r1, r2]       @ r3 = byte all'indirizzo r1 + r2
    cmp r3, #0              @ fine stringa?
    addne r2, r2, #1        @ se no, lunghezza++
    bne conta

    mov r7, #4              @ sys_write(fd = 1, buffer = r1, lunghezza = r2)
    mov r0, #1
    svc 0                   @ al ritorno r0 = byte scritti

    mov r7, #1              @ sys_exit(r0): il codice di uscita è la lunghezza
    svc 0

.data
messaggio: .asciz "Architettura dei calcolatori\n"
