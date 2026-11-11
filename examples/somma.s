@ somma.s — Ciclo con contatore: somma dei numeri da 1 a 10.
@ Il risultato (55) diventa il codice di uscita del programma: lo si legge
@ nella console come exit(55). Prova a mettere un breakpoint su "add".

.text
.global _start

_start:
    mov r0, #0              @ r0 = somma parziale
    mov r1, #1              @ r1 = contatore i

ciclo:
    add r0, r0, r1          @ somma += i
    add r1, r1, #1          @ i++
    cmp r1, #10             @ confronta i con 10 (aggiorna i flag N Z C V)
    ble ciclo               @ se i <= 10 ripeti

    mov r7, #1              @ sys_exit(r0)
    svc 0
