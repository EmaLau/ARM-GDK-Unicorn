.text

.global _start

_start:
    mov r7, #4              @ System call code (WRITE)
    mov r0, #0              @ File descriptor (STDOUT)
    ldr r1, =hello          @ Buffer
    mov r2, #hello_len      @ Count
    svc 0

    mov r7, #1              @ System call code (EXIT)
    mov r0, #0              @ Exit code (0)
    svc 0

.data

hello:
    .ascii "Hello, World!!!!!!!!!!!!!\n"
hello_len = (. - hello)
