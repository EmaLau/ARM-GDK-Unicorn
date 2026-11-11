@ flag.s — I flag N, Z, C, V del CPSR.
@ Esegui con Step e guarda i flag sotto i registri dopo ogni istruzione
@ con il suffisso S (adds, subs) o dopo cmp.

.text
.global _start

_start:
    mov r0, #5
    subs r1, r0, #5         @ 5 - 5 = 0          -> Z = 1, C = 1 (nessun prestito)
    subs r1, r0, #6         @ 5 - 6 = -1         -> N = 1, C = 0 (prestito)
    mvn r2, #0              @ r2 = 0xFFFFFFFF
    adds r3, r2, #1         @ 0xFFFFFFFF + 1 = 0 -> Z = 1, C = 1 (riporto)
    ldr r4, =0x7FFFFFFF     @ il più grande intero positivo a 32 bit con segno
    adds r5, r4, #1         @ diventa negativo   -> N = 1, V = 1 (overflow con segno)
    cmp r0, #3              @ cmp è una subs che non salva il risultato: 5 - 3 > 0

    mov r0, #0
    mov r7, #1              @ sys_exit(0)
    svc 0
