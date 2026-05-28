.text

.global _start

@ Programma di esempio: stampa una stringa su stdout e termina.
@ Utilizza le syscall Linux ARMv7 (interfaccia EABI).

_start:
    mov r7, #4              @ Syscall numero 4 = sys_write
    mov r0, #1              @ File descriptor 1 = stdout
    ldr r1, =hello          @ r1 = puntatore al buffer della stringa
    mov r2, #hello_len      @ r2 = numero di byte da scrivere
    svc 0                   @ Invoca il kernel tramite interrupt software

    mov r7, #1              @ Syscall numero 1 = sys_exit
    mov r0, #0              @ Codice di uscita 0 (successo)
    svc 0                   @ Invoca il kernel per terminare il processo

.data

@ Stringa da stampare su stdout
hello:
    .ascii "Hello, World!!!!!!!!!!!!!ciaoneeeeee\n"
hello_len = (. - hello)     @ Costante calcolata a compile-time: lunghezza in byte della stringa
