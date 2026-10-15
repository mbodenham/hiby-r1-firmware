#!/usr/bin/env python3
"""PoC tap-hook patch for usr/bin/hiby_player (Phase B of the random-album
plan, .kilo/plan.md).

Patches the hglview touch-up select path so every item select logs one line
to the serial console before the stock select callback runs:

    (GUI:E)lg_app_cmn: RSEL it=<item> rec=<w1> <w2> <w3> <w4>

Decode backing this (docs/M2-re-notes-random-album.md, SDK repo):
  - 0x4335c0(list, event, 6) = touch-up: after the 5-px tap gate, the item
    hit-test 0x4326e0(s1->8) hits at 0x433720; the hit item pointer is in v0
    and the selected item's 4-word record (item+128..140) is loaded right
    after. Patch site 0x433734 replaces `lw a1,128(v0)` with `jal stub`;
    the stub logs (item, w1..w4) via the app logger 0x465fa0(tag, fmt, ...)
    and re-executes the replaced load before returning.
  - The logger prints "(GUI:E)%s: %s" (fmt 0x7648e8) with vsprintf, varargs
    from a2/a3/16(sp)/20(sp)/24(sp).

Stub placement: the 160-byte zero gap between LOAD1's p_filesz end
(file 0x43ae60 = vaddr 0x83ae60, right after .plt) and LOAD2's file start
(0x43b000). The gap lies in the same R+E pages LOAD1 already maps, so the
patch extends LOAD1 p_filesz/p_memsz by 0xa0 bytes and writes the stub
there. No code-cave scan needed; nothing else uses the gap.

Usage: python3 tools/patch_random_poc.py [--revert] [path-to-hiby_player]
Idempotent: refuses to patch an already-patched binary; --revert restores
the original bytes (kept here) and the phdr sizes.
"""
import shutil
import sys

STUB_VADDR = 0x83AE60
PATCH_VADDR = 0x433734  # lw a1,128(v0) inside the select path
PATCH_FILE = PATCH_VADDR - 0x400000
GAP_FILE = 0x43AE60
GAP_LEN = 0x43B000 - 0x43AE60
PHDR_LOAD1 = 52 + 3 * 32  # PHDR, INTERP, ABIFLAGS, then LOAD R+E
ORIG_INSN = bytes.fromhex("8000458c")  # lw a1,128(v0)

FMT = b"RSEL it=%08x rec=%08x %08x %08x %08x\n\x00"

# 22 instructions, see module docstring.
JAL_STUB = (0x0C000000 | ((STUB_VADDR >> 2) & 0x3FFFFFF)).to_bytes(4, "little")
STUB = [
    0x27BDFFC0,  # addiu sp,sp,-64
    0xAFBF003C,  # sw   ra,60(sp)
    0xAFA20038,  # sw   v0,56(sp)
    0x3C050084,  # lui  a1,0x84          (fmt @ 0x83aeb8, low half neg.)
    0x24A5AEB8,  # addiu a1,a1,-20872
    0x00403025,  # move a2,v0            (arg3 = item)
    0x8C470080,  # lw   a3,128(v0)       (arg4 = rec w1)
    0x8C490084,  # lw   t1,132(v0)
    0xAF290010,  # sw   t1,16(sp)        (arg5 = rec w2)
    0x8C490088,  # lw   t1,136(v0)
    0xAF290014,  # sw   t1,20(sp)        (arg6 = rec w3)
    0x8C49008C,  # lw   t1,140(v0)
    0xAF290018,  # sw   t1,24(sp)        (arg7 = rec w4)
    0x3C040076,  # lui  a0,0x76          (tag "lg_app_cmn" 0x758644)
    0x24848644,  # addiu a0,a0,-31164
    0x0C1197E8,  # jal  465fa0           (app logger)
    0x00000000,  # nop
    0x8FA20038,  # lw   v0,56(sp)
    0x8C450080,  # lw   a1,128(v0)       (re-executes the replaced load)
    0x8FBF003C,  # lw   ra,60(sp)
    0x03E00008,  # jr   ra
    0x27BD0040,  # addiu sp,sp,64        (delay slot)
]


def stub_bytes():
    out = bytearray()
    for w in STUB:
        out += w.to_bytes(4, "little")
    assert len(out) == 88, len(out)
    out += FMT.ljust(GAP_LEN - len(out), b"\x00")[: GAP_LEN - len(out)]
    return bytes(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    revert = "--revert" in sys.argv
    path = args[0] if args else "src/squashfs-root/usr/bin/hiby_player"

    with open(path, "rb") as f:
        data = bytearray(f.read())
    if len(data) < GAP_FILE + GAP_LEN:
        sys.exit(f"{path}: too small ({len(data)} bytes)")

    patched_insn = data[PATCH_FILE : PATCH_FILE + 4]
    stub_region = bytes(data[GAP_FILE : GAP_FILE + 4])

    if revert:
        if stub_region != stub_bytes()[:4]:
            sys.exit("revert: stub not present, nothing to do")
        data[PATCH_FILE : PATCH_FILE + 4] = ORIG_INSN
        data[GAP_FILE : GAP_FILE + GAP_LEN] = b"\x00" * GAP_LEN
        data[PHDR_LOAD1 + 16 : PHDR_LOAD1 + 24] = (0x43AE60).to_bytes(
            4, "little"
        ) * 2
    else:
        if patched_insn == JAL_STUB and stub_region == stub_bytes()[:4]:
            print("already patched")
            return
        if patched_insn != ORIG_INSN:
            sys.exit(
                f"patch site mismatch at {PATCH_VADDR:#x}: "
                f"found {patched_insn.hex(' ')}"
            )
        if any(data[GAP_FILE : GAP_FILE + GAP_LEN]):
            sys.exit("gap is not zero — refusing to overwrite")
        data[GAP_FILE : GAP_FILE + GAP_LEN] = stub_bytes()
        data[PATCH_FILE : PATCH_FILE + 4] = JAL_STUB
        data[PHDR_LOAD1 + 16 : PHDR_LOAD1 + 24] = (0x43B000).to_bytes(
            4, "little"
        ) * 2

    tmp = path + ".patched"
    with open(tmp, "wb") as f:
        f.write(data)
    shutil.move(tmp, path)
    print(("reverted" if revert else "patched") + f": {path}")
    print(f"  stub @ {STUB_VADDR:#x} ({GAP_LEN} B gap), "
          f"hook @ {PATCH_VADDR:#x}: jal {STUB_VADDR:#x}")


if __name__ == "__main__":
    main()
