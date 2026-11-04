"""The compiled code of a C compiler in the flat mode (step 3a3): functions of a C file, built by gcc for the 386 and
run in the emulator above the first megabyte, with many inputs each. What they must give is worked out by Python
(zlib, math, integers) and by the rules of C, so the emulator is checked against what a real CPU does.

The C file, funcs.c (in the build of the functions of the tests, `u32` is unsigned, `s32` is int and so on):

/* Functions for the tests of the flat mode: each is a plain C function, called with the cdecl convention. */
typedef unsigned char u8;
typedef signed char s8;
typedef unsigned short u16;
typedef short s16;
typedef unsigned u32;
typedef int s32;
typedef unsigned long long u64;
typedef long long s64;

u32 crc32(const u8 *p, u32 n) {
    u32 c = 0xFFFFFFFFu;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & -(c & 1));
    }
    return ~c;
}

void isort(s32 *a, s32 n) {
    for (s32 i = 1; i < n; i++) {
        s32 v = a[i], j = i - 1;
        while (j >= 0 && a[j] > v) { a[j + 1] = a[j]; j--; }
        a[j + 1] = v;
    }
}

u64 mul64(u32 a, u32 b) { return (u64)a * b; }

u64 mix64(u64 x) {
    x ^= x >> 33;
    x *= 0xFF51AFD7ED558CCDull;
    x ^= x >> 29;
    return x;
}

s32 divmod_s(s32 a, s32 b, s32 *r) { *r = a % b; return a / b; }

u32 divmod_u(u32 a, u32 b, u32 *r) { *r = a % b; return a / b; }

u32 popcount(u32 x) { u32 n = 0; while (x) { n += x & 1; x >>= 1; } return n; }

u32 log2floor(u32 x) { return 31 - __builtin_clz(x); }

u32 swap32(u32 x) { return __builtin_bswap32(x); }

s32 strcmp_s(const s8 *a, const s8 *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a - *b;
}

u32 strlen_s(const char *s) { const char *p = s; while (*p) p++; return p - s; }

s32 sw(s32 x) {
    switch (x) {
    case 0: return 11;
    case 1: return 22;
    case 2: return 35;
    case 3: return -4;
    case 4: return 500;
    case 5: return 61;
    case 6: return 70;
    case 7: return 8888;
    default: return x * 3;
    }
}

u32 collatz(u32 n) { u32 s = 0; while (n != 1) { n = (n & 1) ? 3 * n + 1 : n >> 1; s++; } return s; }

s32 sar(s32 x, s32 n) { return x >> n; }

u32 shr(u32 x, u32 n) { return x >> n; }

u32 rotl(u32 x, u32 n) { return (x << (n & 31)) | (x >> ((32 - n) & 31)); }

s32 fixmul(s32 a, s32 b) { return (s32)(((s64)a * b) >> 16); }

s32 sumbytes(const s8 *p, s32 n) { s32 s = 0; while (n--) s += *p++; return s; }

u32 sumwords(const u16 *p, s32 n) { u32 s = 0; while (n--) s += *p++; return s; }

s32 clampsum(const s16 *p, s32 n) {
    s32 s = 0;
    for (s32 i = 0; i < n; i++) { s += p[i]; if (s > 1000) s = 1000; if (s < -1000) s = -1000; }
    return s;
}

struct S { s32 a; s16 b; u8 c[10]; u32 d; };

u32 copystruct(struct S *dst, const struct S *src) { *dst = *src; return dst->a + dst->b + dst->c[9] + dst->d; }

s32 maxmin(const s32 *p, s32 n) {
    s32 hi = p[0], lo = p[0];
    for (s32 i = 1; i < n; i++) { if (p[i] > hi) hi = p[i]; if (p[i] < lo) lo = p[i]; }
    return hi - lo;
}

s32 gcd_s(s32 a, s32 b) { while (b) { s32 t = a % b; a = b; b = t; } return a < 0 ? -a : a; }

u32 isqrt(u32 x) {
    u32 r = 0, bit = 1u << 30;
    while (bit > x) bit >>= 2;
    while (bit) {
        if (x >= r + bit) { x -= r + bit; r = (r >> 1) + bit; } else r >>= 1;
        bit >>= 2;
    }
    return r;
}

s32 abs_diff(s32 a, s32 b) { return a > b ? a - b : b - a; }

u32 compare_flags(u32 a, u32 b) {
    return (a < b) | ((a <= b) << 1) | (((s32)a < (s32)b) << 2) | (((s32)a <= (s32)b) << 3) | ((a == b) << 4);
}
"""
import math
import random
import struct
import unittest
import zlib

from dosloth.cpu import CPU, Exit
from dosloth.flat import Bases

FLAT = 0x10
BASE = 0x400000  # where the code is linked
STUB = 0x3F0000  # the return address of every call: int 21h, which ends the run and leaves the registers as they are
STACK = 0x00900000
DATA = 0x00600000
M32, M64 = 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF

# The same file built four ways, as `gcc -m32 -OPT -march=i486 -mno-sse -ffreestanding -fno-builtin -fno-pic
# -fno-asynchronous-unwind-tables -fcf-protection=none -fno-stack-protector -c funcs.c`, then linked with
# `ld -m elf_i386 -Ttext=0x400000 --oformat binary`. A build is the pieces of the file that are not zeros (the offset
# and the bytes of each: the code, and the data of the program after it), the offsets of the functions, and the
# size of the code.
BUILDS = {
    "O0": {
        "pieces": [
            (0x0, bytes.fromhex(
                "5589e583ec10c745fcffffffffeb3e8b45088d50018955088a0025ff0000003145fcc745f800000000eb1c8b45fc"
                "d1e889c28b45fc83e001f7d8252083b8ed31d08945fcff45f8837df8077ede8b450c8d50ff89550c85c075b58b45"
                "fcf7d089ec5dc35589e583ec10c745fc01000000eb778b45fc8d1485000000008b450801d08b008945f48b45fc48"
                "8945f8eb268b45f88d1485000000008b450801d08b55f8428d0c95000000008b550801ca8b008902ff4df8837df8"
                "0078168b45f88d1485000000008b450801d08b003945f47cbe8b45f8408d1485000000008b450801c28b45f48902"
                "ff45fc8b45fc3b450c7c81909089ec5dc35589e55756538b4508ba000000008b4d0cbb0000000089d70faff989de"
                "0faff001fef7e18d0c1689ca5b5e5f5dc35589e583ec088b45088945f88b450c8945fc8b45f88b55fc89d031d2d1"
                "e83145f83155fc8b45fc69d0cd8c55ed8b45f869c0d7af51ff8d0c028b45f8bacd8c55edf7e201d189ca8945f889"
                "55fc8945f88955fc8b45f88b55fc0facd01dc1ea1d3145f83155fc8b45f88b55fc89ec5dc35589e58b450899f77d"
                "0c8b451089108b450899f77d0c5dc35589e58b4508ba00000000f7750c8b451089108b4508ba00000000f7750c5d"
                "c35589e583ec10c745fc00000000eb0c8b450883e0010145fcd16d08837d080075ee8b45fc89ec5dc35589e50fbd"
                "450883f01fba1f00000029c289d05dc35589e58b45080fc85dc35589e5eb06ff4508ff450c8b45088a0084c0740e"
                "8b45088a108b450c8a0038c274e38b45088a000fbed08b450c8a000fbec029c289d05dc35589e583ec108b450889"
                "45fceb03ff45fc8b45fc8a0084c075f48b45fc2b450889ec5dc35589e5837d08070f8485000000837d08070f8f82"
                "000000837d0806746e837d08067f76837d0805745b837d08057f6a837d08047448837d08047f5e837d0803743583"
                "7d08037f52837d08027422837d08027f46837d08007408837d08017409eb38b80b000000eb3ab816000000eb33b8"
                "23000000eb2cb8fcffffffeb25b8f4010000eb1eb83d000000eb17b846000000eb10b8b8220000eb098b450889c2"
                "01d201d05dc35589e583ec10c745fc00000000eb1f8b450883e00185c0740f8b450889c201d201d040894508eb03"
                "d16d08ff45fc837d080175db8b45fc89ec5dc35589e58b450c8b550888c1d3fa89d05dc35589e58b450c8b550888"
                "c1d3ea89d05dc35589e5538b450c83e01f8b550889d388c1d3e38b450cf7d883e01f8b550888c1d3ea89d009d85b"
                "5dc35589e55756538b4508998b4d0c89cbc1fb1f89d70faff989de0faff001fef7e18d0c1689ca0facd010c1fa10"
                "5b5e5f5dc35589e583ec10c745fc00000000eb118b45088d50018955088a000fbec00145fc8b450c8d50ff89550c"
                "85c075e28b45fc89ec5dc35589e583ec10c745fc00000000eb148b45088d5002895508668b0025ffff00000145fc"
                "8b450c8d50ff89550c85c075df8b45fc89ec5dc35589e583ec10c745fc00000000c745f800000000eb368b45f801"
                "c089c28b450801d0668b00980145fc817dfce80300007e07c745fce8030000817dfc18fcffff7d07c745fc18fcff"
                "ffff45f88b45f83b450c7cc28b45fc89ec5dc35589e55756538b55088b450c89c3b80500000089d789de89c1f3a5"
                "8b45088b108b45088b40049801c28b45088a400f25ff00000001d089c28b45088b401001d05b5e5f5dc35589e583"
                "ec108b45088b008945fc8b45088b008945f8c745f401000000eb578b45f48d1485000000008b450801d08b003945"
                "fc7d148b45f48d1485000000008b450801d08b008945fc8b45f48d1485000000008b450801d08b003945f87e148b"
                "45f48d1485000000008b450801d08b008945f8ff45f48b45f43b450c7ca18b45fc2b45f889ec5dc35589e583ec10"
                "eb168b450899f77d0c8955fc8b450c8945088b45fc89450c837d0c0075e48b450885c07902f7d889ec5dc35589e5"
                "83ec10c745fc00000000c745f800000040eb04c16df8028b45f839450872f4eb308b55fc8b45f801d0394508721c"
                "8b55fc8b45f801d02945088b45fcd1e889c28b45f801d08945fceb03d16dfcc16df802837df80075ca8b45fc89ec"
                "5dc35589e58b45083b450c7e088b45082b450ceb068b450c2b45085dc35589e58b45083b450c0f92c031d288c28b"
                "450839450c7207b802000000eb05b80000000009d089c18b55088b450c39c27d07b804000000eb05b80000000009"
                "c18b55088b450c39c27f07b808000000eb05b80000000009c889c28b45083b450c7507b810000000eb05b8000000"
                "0009d05dc3"
            )),
        ],
        "symbols": {
            "crc32": 0x0, "isort": 0x63, "mul64": 0xF7, "mix64": 0x125,
            "divmod_s": 0x195, "divmod_u": 0x1AD, "popcount": 0x1CD, "log2floor": 0x1F5,
            "swap32": 0x20A, "strcmp_s": 0x214, "strlen_s": 0x24C, "sw": 0x270,
            "collatz": 0x314, "sar": 0x34F, "shr": 0x360, "rotl": 0x371,
            "fixmul": 0x39A, "sumbytes": 0x3CB, "sumwords": 0x3FF, "clampsum": 0x436,
            "copystruct": 0x491, "maxmin": 0x4D6, "gcd_s": 0x55E, "isqrt": 0x58F,
            "abs_diff": 0x5F0, "compare_flags": 0x60B,
        },
        "text_size": 1661,
    },
    "O1": {
        "pieces": [
            (0x0, bytes.fromhex(
                "56538b5c240c8b74241085f6743001deb8ffffffff4331d28a53ff31d0ba0800000089c1d1e983e001f7d8252083"
                "b8ed31c84a75ed39de75dcf7d05b5ec3b8ffffffffebf45756538b4c2410837c2414017e3a8b4424148d78ffbe00"
                "000000eb09895c81044639fe74238b5cb10489f085f678138b148139da7ee6895481044883f8ff75efebda46891c"
                "b1ebdd5b5e5fc38b442404f7642408c356538b4c240c8b5c241089c889da89d031d2d1e831c831da69cacd8c55ed"
                "69d0d7af51ff01d1bacd8c55edf7e201ca89c189d30facd91dc1eb1d31c831da5b5ec38b44240499f77c24088b4c"
                "240c8911c38b442404ba00000000f77424088b4c240c8911c38b44240485c07413ba0000000089c183e10101cad1"
                "e875f589d0c389c2ebf90fbd54240483f21fb81f00000029d0c38b4424040fc8c38b4c24048b5424088a0184c074"
                "0c3802750841428a0184c075f40fbec00fbe1229d0c38b542404803a00740b89d04080380075fa29d0c389d0ebf9"
                "8b54240483fa077731ff249500104000b80b000000c3b823000000c3b8fcffffffc3b8f4010000c3b83d000000c3"
                "b846000000c3b8b8220000c38d041201d0c3b816000000c38b44240483f801741dba00000000eb08d1e84283f801"
                "7413a80174f48d0c008d44080142ebf2ba0000000089d0c38b4c24088b442404d3f8c38b4c24088b442404d3e8c3"
                "8b4c24088b442404d3c0c3555756538b4424148b74241889c3c1fb1f89f5c1fd1f89da0fafd689e90fafc801d1f7"
                "e601ca89c10facd11089c85b5e5f5dc3538b5424088b44240c85c074138d1c02b800000000420fbe4aff01c839d3"
                "75f55bc3538b4424088b5c240c85db741c01db01c3ba0000000083c00231c9668b48fe01ca39d875f189d05bc3ba"
                "00000000ebf5538b44240c85c07e378b54240801c001d089c3b800000000eb0783c20239da74240fbf0a01c83d18"
                "fcffff7d05b818fcffff3de80300007ee1b8e8030000ebdab8000000005bc357568b54240c8b742410b905000000"
                "89d7f3a50fbf4204030231c98a4a0f01c80342105e5fc356538b4c240c8b5c24108b0183fb017e218d51048d3499"
                "89c3eb0783c20439f274128b0a39c87d0289c839cb7eed89cbebe989c329d85b5ec3538b4424088b4c240c85c974"
                "1589cb99f7f989d189d885d275f389d885db78065bc389c3ebf4f7d8ebf6538b4c240881f9ffffff3f7717ba0000"
                "0040c1ea0239d172f985d27424b800000000eb0eba00000040ebf2d1e8c1ea0274118d1c1039d972f229d9d1e801"
                "d0ebec89d05bc38b4c24048b54240839d17e0589c829d0c389d029c8c3538b4c24088b54240c39d10f94c025ff00"
                "0000c1e00439ca0f93c381e3ff00000001db09d839d10f92c381e3ff00000009d839d10f9cc381e3ff000000c1e3"
                "0209d839d10f9ec281e2ff000000c1e20309d05bc3"
            )),
            (0x1000, bytes.fromhex(
                "80014000b0014000860140008c01400092014000980140009e014000a40140"
            )),
        ],
        "symbols": {
            "crc32": 0x0, "isort": 0x45, "mul64": 0x91, "mix64": 0x9A,
            "divmod_s": 0xDB, "divmod_u": 0xEB, "popcount": 0xFF, "log2floor": 0x11E,
            "swap32": 0x12E, "strcmp_s": 0x135, "strlen_s": 0x158, "sw": 0x170,
            "collatz": 0x1B6, "sar": 0x1E4, "shr": 0x1EF, "rotl": 0x1FA,
            "fixmul": 0x205, "sumbytes": 0x238, "sumwords": 0x25A, "clampsum": 0x28A,
            "copystruct": 0x2D1, "maxmin": 0x2F7, "gcd_s": 0x330, "isqrt": 0x35A,
            "abs_diff": 0x39F, "compare_flags": 0x3B5,
        },
        "text_size": 1033,
    },
    "O2": {
        "pieces": [
            (0x0, bytes.fromhex(
                "56538b5c240c8b74241085f6743e01deb8ffffffff8d76002e8db426000000004331d28a53ff31d0ba080000008d"
                "760089c1d1e983e001f7d8252083b8ed31c84a75ed39f375d9f7d05b5ec331c05b5ec38db426000000002e8db426"
                "00000000555756538b6c2414837c2418017e40bf010000008d7426002e8db426000000008b74bd008d57ff8d44bd"
                "002e8d74260089c38b48fc39f17e0d89084a8d43fc83faff75ec89eb893347397c241875d15b5e5f5dc38d742600"
                "2e8db426000000008b442404f7642408c38db4260000000056538b4c240c8b5c241089c889da89d031d2d1e831c8"
                "31da89c369cacd8c55ed69d0d7af51ff01d1b8cd8c55edf7e301ca89c189d30facd91dc1eb1d31c831da5b5ec32e"
                "8d7426002e8db426000000008b44240499f77c24088b4c240c8911c38b44240431d2f77424088b4c240c8911c38d"
                "b426000000002e8db426000000008b44240431d285c074118db60000000089c183e10101cad1e875f589d0c36690"
                "0fbd442404c366902e8db426000000008b4424040fc8c3902e8db42600000000538b5424088b5c240c0fbe0284c0"
                "7519eb388db6000000002e8db42600000000420fbe0284c07418430fbe0b38c174f029c85bc38d7426002e8db426"
                "000000000fbe4b0131c029c85bc30fbe0b31c0ebdf8db426000000002e8db426000000008b542404803a00741789"
                "d02e8d7426004080380075fa29d0c38db4260000000031c0c32e8d7426002e8db426000000008b54240483fa0777"
                "178b049500104000c38db426000000002e8db426000000008d041201d0c366902e8db426000000008b44240431d2"
                "83f80174192e8d742600a80174088d0c008d44080142d1e84283f80175ec89d0c3902e8db426000000008b4c2408"
                "8b442404d3f8c32e8d7426008b4c24088b442404d3e8c32e8d7426008b4c24088b442404d3c0c32e8d7426008b44"
                "2404f76c240889c10facd11089c8c38db426000000002e8db42600000000538b4424088b5c240c85db742301c331"
                "d28db426000000002e8db42600000000400fbe48ff01ca39c375f589d05bc39031d289d05bc366902e8db4260000"
                "0000538b4424088b5c240c85db743301db01c331d22e8d7426002e8db4260000000083c00231c9668b48fe01ca39"
                "c375f189d05bc32e8d7426002e8db4260000000031d289d05bc366902e8db42600000000538b5c240c85db7e478b"
                "54240801db01d331c0eb178d76002e8db42600000000b818fcffff83c20239d3741f0fbf0a01c83d18fcffff7ce8"
                "3de80300007ee6b8e803000083c20239d375e15bc38d760031c05bc38d7426002e8db4260000000057568b54240c"
                "8b742410b90500000089d7f3a50fbf4204030231c98a4a0f01c80342105e5fc366902e8db4260000000056538b4c"
                "240c8b5c241083fb017e318b018d51048d349989c38db426000000008b0a39c87d0289c839cb7e0289cb83c20439"
                "d675eb29d85b5ec38db60000000031c05b5ec38d76002e8db42600000000538b4424088b54240c85d274338d7600"
                "89d199f7f989c885d275f589c885c9780f5bc32e8d7426002e8db42600000000f7d85bc38d7426002e8db4260000"
                "000089c189c885c979d9ebe68db600000000538b4c2408b80000004081f9ffffff3f77198db6000000002e8db426"
                "00000000c1e80239c172f985c0742531d28d76008d1c10d1ea39d9720429d901c2c1e80275ee89d05bc366902e8d"
                "b4260000000031d289d05bc366902e8db426000000008b4c24048b54240839d17e1489c829d0c38db42600000000"
                "2e8db4260000000089d029c8c38d76002e8db42600000000538b4c24088b54240c31c039d10f92c039ca732c39d1"
                "7d4883c804bb0800000039d10f94c281e2ff000000c1e20409d809d05bc38d7426002e8db4260000000083c80239"
                "d17cd10f94c381e3ff000000c1e303ebcb8d76002e8db4260000000075d083c8085bc3"
            )),
            (0x1000, bytes.fromhex(
                "0b0000001600000023000000fcfffffff40100003d00000046000000b822"
            )),
        ],
        "symbols": {
            "crc32": 0x0, "isort": 0x60, "mul64": 0xC0, "mix64": 0xD0,
            "divmod_s": 0x120, "divmod_u": 0x130, "popcount": 0x150, "log2floor": 0x170,
            "swap32": 0x180, "strcmp_s": 0x190, "strlen_s": 0x1F0, "sw": 0x220,
            "collatz": 0x250, "sar": 0x280, "shr": 0x290, "rotl": 0x2A0,
            "fixmul": 0x2B0, "sumbytes": 0x2D0, "sumwords": 0x310, "clampsum": 0x360,
            "copystruct": 0x3C0, "maxmin": 0x3F0, "gcd_s": 0x440, "isqrt": 0x490,
            "abs_diff": 0x4F0, "compare_flags": 0x520,
        },
        "text_size": 1415,
    },
    "Os": {
        "pieces": [
            (0x0, bytes.fromhex(
                "5589e556538b55088b5d0c01d3b8ffffffff39da7421420fb64aff31c8b90800000089c6d1ee83e001f7d8252083"
                "b8ed31f04975edebdbf7d05b5e5dc35589e55756538b5508b8010000003b450c7d268b34828d58ff89d98b3c9a39"
                "f77e10897c9a048d59ff85c975ecb9ffffffff89748a0440ebd55b5e5f5dc35589e58b4508f7650c5dc35589e556"
                "538b4d088b5d0c89c889da89d031d2d1e831c831da89c369cacd8c55ed69d0d7af51ff01d1b8cd8c55edf7e301ca"
                "89c189d30facd91dc1eb1d31c831da5b5e5dc35589e58b450899f77d0c8b4d1089115dc35589e58b450831d2f775"
                "0c8b4d1089115dc35589e58b450831d285c0740b89c183e10101cad1e8ebf189d05dc35589e50fbd45085dc35589"
                "e58b45080fc85dc35589e531c98b45080fbe04088b550c0fbe140a84c074054138d074e929d05dc35589e58b5508"
                "89d0803800740340ebf829d05dc35589e58b45088d144083f80777080fbf94000010400089d05dc35589e58b4508"
                "31d283f801740ea80174058d04404042d1e842ebed89d05dc35589e58b4d0c8b4508d3f85dc35589e58b4d0c8b45"
                "08d3e85dc35589e58b4d0c8b4508d3c05dc35589e58b4508f76d0c89c10facd11089c85dc35589e5538b45088b4d"
                "0c01c131d239c87409400fbe58ff01daebf389d05b5dc35589e5538b55088b450c01c001d031c939c2740b83c202"
                "0fb75afe01d9ebf189c85b5dc35589e5538b4d0c31d231c039ca7d268b5d080fbf1c5301d83d18fcffff7d07b818"
                "fcffffeb0c3de80300007e05b8e803000042ebd65b5dc35589e557568b55088b750cb90500000089d7f3a50fbf42"
                "0403020fb64a0f01c80342105e5f5dc35589e556538b75088b0689c3ba010000003b550c7d128b0c9639c87d0289"
                "c839cb7e0289cb42ebe929d85b5e5dc35589e58b45088b4d0c9985c97408f7f989c889d1ebf331d029d05dc35589"
                "e5538b4d08b80000004039c17305c1e802ebf731d285c074128d1c02d1ea39d9720429d901c2c1e802ebea89d05b"
                "5dc35589e58b55088b450c89c129d139c27e0429c289d189c85dc35589e5538b5d088b450c31d239d80f93c201d2"
                "31c939c30f92c109ca39c37d0a83ca04b908000000eb090f94c10fb6c9c1e10339c30f94c00fb6c0c1e00409d109"
                "c85b5dc3"
            )),
            (0x1000, bytes.fromhex(
                "0b0016002300fcfff4013d004600b822"
            )),
        ],
        "symbols": {
            "crc32": 0x0, "isort": 0x3D, "mul64": 0x7B, "mix64": 0x86,
            "divmod_s": 0xCB, "divmod_u": 0xDC, "popcount": 0xEE, "log2floor": 0x109,
            "swap32": 0x112, "strcmp_s": 0x11C, "strlen_s": 0x13C, "sw": 0x150,
            "collatz": 0x16A, "sar": 0x189, "shr": 0x196, "rotl": 0x1A3,
            "fixmul": 0x1B0, "sumbytes": 0x1C3, "sumwords": 0x1E3, "clampsum": 0x207,
            "copystruct": 0x23F, "maxmin": 0x266, "gcd_s": 0x294, "isqrt": 0x2B0,
            "abs_diff": 0x2E2, "compare_flags": 0x2FB,
        },
        "text_size": 832,
    },
}


def s32(value):
    value &= M32
    return value - (1 << 32) if value >> 31 else value


def s8(value):
    return value - 256 if value & 0x80 else value


def c_div(a, b):
    """The quotient and the remainder of C: the quotient is cut towards zero, the remainder has the sign of a."""
    q = abs(a) // abs(b)
    q = q if (a < 0) == (b < 0) else -q
    return q, a - q * b


def end_of_the_run(cpu):
    raise Exit(0)


class Compiled(unittest.TestCase):
    """Calls the functions of the C file in the flat mode, as a program would: the arguments on the stack, the
    return address on top of them, the result in EAX (and EDX for a 64-bit one)."""

    BUILD = "O2"

    def setUp(self):
        cpu = self.cpu = CPU()
        cpu.enter_flat(Bases({FLAT: 0}))
        build = BUILDS[self.BUILD]
        self.symbols, self.text_size = build["symbols"], build["text_size"]
        for offset, data in build["pieces"]:
            cpu.mem[BASE + offset:BASE + offset + len(data)] = data
        cpu.mem[STUB:STUB + 2] = bytes([0xCD, 0x21])
        cpu.int_handlers[0x21] = end_of_the_run
        self.steps = 0

    def call(self, name, *args, limit=3_000_000):
        """Run the function with its arguments (integers of 32 bits). Returns (EAX, EDX)."""
        cpu = self.cpu
        cpu.s[:] = [FLAT] * 6
        cpu.r[:] = [0] * 8
        cpu.rh[:] = [0] * 8
        cpu.flags = 0x0202
        sentinels = {"ebx": 0xB1B2B3B4, "esi": 0x51525354, "edi": 0xD1D2D3D4, "ebp": 0xE1E2E3E4}  # to be given back
        for register, value in sentinels.items():
            cpu.set32(("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi").index(register), value)
        cpu.set32(CPU.SP, STACK)
        for argument in reversed(args):
            cpu.push32(argument & M32)
        cpu.push32(STUB)
        cpu.ip = BASE + self.symbols[name]
        start_sp = cpu.get32(CPU.SP)
        for count in range(limit):
            try:
                cpu.step()
            except Exit:
                break
        else:
            self.fail(f"{name} did not return")
        self.steps += count
        self.assertEqual(cpu.get32(CPU.SP), start_sp + 4, f"{name}: the stack is not as it was")  # the return took 4
        for register, value in sentinels.items():
            index = ("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi").index(register)
            self.assertEqual(cpu.get32(index), value, f"{name} did not give back {register}")
        return cpu.get32(CPU.AX), cpu.get32(CPU.DX)

    def put(self, address, data):
        self.cpu.mem[address:address + len(data)] = data

    def dword(self, address):
        return struct.unpack_from("<I", self.cpu.mem, address)[0]


class Functions:
    """The tests of the functions: for each build, a TestCase made of this and Compiled (see the end)."""

    def setUp(self):
        super().setUp()
        self.random = random.Random(386)

    def numbers(self, count=12):
        extremes = [0, 1, 2, 3, 0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0xFFFF, 0x10000, 0x7FFFFFFF, 0x80000000,
                    0x80000001, 0xFFFFFFFE, 0xFFFFFFFF]
        return extremes + [self.random.getrandbits(32) for _ in range(count)]

    def test_crc32_agrees_with_zlib(self):
        for n in (0, 1, 2, 3, 7, 8, 64, 100):
            data = bytes(self.random.getrandbits(8) for _ in range(n))
            self.put(DATA, data)
            self.assertEqual(self.call("crc32", DATA, n)[0], zlib.crc32(data), n)

    def test_the_insertion_sort_sorts_signed_numbers(self):
        for n in (0, 1, 2, 5, 17, 40):
            values = [s32(v) for v in self.numbers(n)][:n] if n else []
            self.put(DATA, struct.pack(f"<{n}i", *values))
            self.call("isort", DATA, n)
            self.assertEqual(list(struct.unpack(f"<{n}i", self.cpu.mem[DATA:DATA + 4 * n])), sorted(values), n)

    def test_a_64_bit_product_comes_in_edx_eax(self):
        for a in self.numbers(4):
            for b in self.numbers(4):
                eax, edx = self.call("mul64", a, b)
                self.assertEqual(edx << 32 | eax, a * b, f"{a:X}*{b:X}")

    def test_a_64_bit_mix_with_shifts_and_a_64_bit_multiplication(self):
        for _ in range(12):
            x = self.random.getrandbits(64)
            eax, edx = self.call("mix64", x & M32, x >> 32)
            y = x ^ (x >> 33)
            y = y * 0xFF51AFD7ED558CCD & M64
            y ^= y >> 29
            self.assertEqual(edx << 32 | eax, y, f"{x:X}")

    def test_signed_division_cuts_towards_zero_and_the_remainder_has_the_sign_of_the_dividend(self):
        values = [s32(v) for v in self.numbers(6)]
        for a in values:
            for b in values:
                if b == 0 or (a == -(1 << 31) and b == -1):
                    continue
                q, r = c_div(a, b)
                self.assertEqual(s32(self.call("divmod_s", a, b, DATA)[0]), q, f"{a}/{b}")
                self.assertEqual(s32(self.dword(DATA)), r, f"{a}%{b}")

    def test_unsigned_division(self):
        for a in self.numbers(6):
            for b in self.numbers(6):
                if b == 0:
                    continue
                self.assertEqual(self.call("divmod_u", a, b, DATA)[0], a // b, f"{a}/{b}")
                self.assertEqual(self.dword(DATA), a % b, f"{a}%{b}")

    def test_bit_counting_the_highest_bit_and_the_byte_swap(self):
        for x in self.numbers(8):
            self.assertEqual(self.call("popcount", x)[0], bin(x).count("1"), hex(x))
            if x:
                self.assertEqual(self.call("log2floor", x)[0], x.bit_length() - 1, hex(x))
            self.assertEqual(self.call("swap32", x)[0], int.from_bytes(x.to_bytes(4, "little"), "big"), hex(x))

    def test_strings_are_compared_as_signed_characters_and_measured(self):
        words = [b"", b"a", b"abc", b"abd", b"abcd", b"\x80x", b"\x7fx", b"\xffz", b"\xffa", b"same", b"same"]
        for a in words:
            self.assertEqual(self.call("strlen_s", self.put_string(DATA, a))[0], len(a), a)
            for b in words:
                pa, pb = self.put_string(DATA, a), self.put_string(DATA + 0x100, b)
                expected = 0
                for i in range(max(len(a), len(b)) + 1):
                    x = a[i] if i < len(a) else 0
                    y = b[i] if i < len(b) else 0
                    if x == 0 or x != y:
                        expected = s8(x) - s8(y)
                        break
                self.assertEqual(s32(self.call("strcmp_s", pa, pb)[0]), expected, f"{a} {b}")

    def put_string(self, address, text):
        self.put(address, text + b"\0")
        return address

    def test_a_switch(self):
        table = {0: 11, 1: 22, 2: 35, 3: -4, 4: 500, 5: 61, 6: 70, 7: 8888}
        for x in range(-5, 20):
            self.assertEqual(s32(self.call("sw", x)[0]), table.get(x, x * 3), x)

    def test_the_collatz_steps(self):
        def steps(n):
            count = 0
            while n != 1:
                n = 3 * n + 1 if n & 1 else n >> 1
                count += 1
            return count
        for n in list(range(1, 40)) + [97, 871, 6171, 77031]:
            self.assertEqual(self.call("collatz", n)[0], steps(n), n)

    def test_shifts_and_rotations_by_every_count(self):
        for x in (0, 1, 0x80000000, 0xFFFFFFFF, 0x12345678, 0xDEADBEEF):
            for n in range(32):
                self.assertEqual(s32(self.call("sar", x, n)[0]), s32(x) >> n, f"sar {x:X} {n}")
                self.assertEqual(self.call("shr", x, n)[0], x >> n, f"shr {x:X} {n}")
                rotated = (x << n | x >> (32 - n)) & M32 if n else x
                self.assertEqual(self.call("rotl", x, n)[0], rotated, f"rotl {x:X} {n}")

    def test_a_fixed_point_product_with_a_64_bit_intermediate(self):
        for a in [s32(v) for v in self.numbers(6)]:
            for b in [s32(v) for v in self.numbers(6)]:
                self.assertEqual(s32(self.call("fixmul", a, b)[0]), s32((a * b) >> 16), f"{a}*{b}")

    def test_sums_of_signed_bytes_unsigned_words_and_clamped_signed_words(self):
        for n in (0, 1, 5, 33):
            bytes_ = bytes(self.random.getrandbits(8) for _ in range(n))
            self.put(DATA, bytes_)
            self.assertEqual(s32(self.call("sumbytes", DATA, n)[0]), sum(s8(b) for b in bytes_), n)
            words = [self.random.getrandbits(16) for _ in range(n)]
            self.put(DATA, struct.pack(f"<{n}H", *words))
            self.assertEqual(self.call("sumwords", DATA, n)[0], sum(words) & M32, n)
            shorts = [self.random.randint(-700, 700) for _ in range(n)]
            self.put(DATA, struct.pack(f"<{n}h", *shorts))
            total = 0
            for v in shorts:
                total = max(-1000, min(1000, total + v))
            self.assertEqual(s32(self.call("clampsum", DATA, n)[0]), total, n)

    def test_a_structure_is_copied_whole(self):
        source = bytes(self.random.getrandbits(8) for _ in range(20))
        self.put(DATA, source)
        self.put(DATA + 0x100, bytes(20))
        eax = self.call("copystruct", DATA + 0x100, DATA)[0]
        self.assertEqual(bytes(self.cpu.mem[DATA + 0x100:DATA + 0x114]), source)
        a, b = struct.unpack_from("<ih", source, 0)
        d = struct.unpack_from("<I", source, 16)[0]
        self.assertEqual(eax, (a + b + source[15] + d) & M32)

    def test_the_range_of_an_array_the_gcd_the_square_root_and_the_absolute_difference(self):
        for n in (1, 2, 9):
            values = [s32(v) for v in self.numbers(n)][:n]
            self.put(DATA, struct.pack(f"<{n}i", *values))
            self.assertEqual(s32(self.call("maxmin", DATA, n)[0]), s32(max(values) - min(values)), values)
        for a in [s32(v) for v in self.numbers(4)]:
            for b in [s32(v) for v in self.numbers(4)]:
                if {a, b} == {-(1 << 31), -1}:
                    continue  # INT_MIN % -1 overflows the division: a real CPU stops there too
                self.assertEqual(self.call("gcd_s", a, b)[0], math.gcd(abs(a), abs(b)) & M32, f"{a} {b}")
                self.assertEqual(s32(self.call("abs_diff", a, b)[0]), s32(a - b if a > b else b - a), f"{a} {b}")
        for x in self.numbers(8):
            self.assertEqual(self.call("isqrt", x)[0], math.isqrt(x), hex(x))

    def test_every_kind_of_comparison_of_two_numbers(self):
        for a in self.numbers(4):
            for b in self.numbers(4):
                expected = (a < b) | (a <= b) << 1 | (s32(a) < s32(b)) << 2 | (s32(a) <= s32(b)) << 3 | (a == b) << 4
                self.assertEqual(self.call("compare_flags", a, b)[0], expected, f"{a:X} {b:X}")


    def test_the_whole_of_the_code_is_known_to_the_disassembler_and_the_functions_begin_where_the_symbols_say(self):
        from dosloth.disasm import disasm
        starts = set(self.symbols.values())
        at, found = BASE, set()
        while at < BASE + self.text_size:  # the code only: the data of the program follows it
            length, text = disasm(self.cpu, FLAT, at)
            if at - BASE in starts:
                found.add(at - BASE)
            self.assertFalse(text.startswith("db"), f"{at:X}: {text}")
            at += length
        self.assertEqual((found, at), (starts, BASE + self.text_size))


for _build in BUILDS:
    globals()["TestTheFunctions" + _build] = type("TestTheFunctions" + _build, (Functions, Compiled),
                                                  {"BUILD": _build, "__module__": __name__})


if __name__ == "__main__":
    unittest.main()
