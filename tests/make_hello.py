# Hand-assembled .COM: prints "Hello" and exits with code 0.
code = bytes([
    0xBA, 0x0C, 0x01,   # mov dx, 010Ch (string)
    0xB4, 0x09,         # mov ah, 9
    0xCD, 0x21,         # int 21h
    0xB8, 0x00, 0x4C,   # mov ax, 4C00h
    0xCD, 0x21,         # int 21h
]) + b"Hello\r\n$"
open("tests/hello.com", "wb").write(code)
