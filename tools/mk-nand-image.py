#!/usr/bin/env python3
"""Build a QEMU x1600 SPI-NAND backing image from a firmware build.

Same layout as the SDK's tools/x1600/make_nand_image.py (docs: SDK
ubifs-mount-log / M2), parameterized so a repacked firmware (.upt
unpacked, or src/) can be booted in the vendored QEMU:

  mtd0 uboot    2 MiB   (erased)
  mtd1 kernel   5 MiB   xImage
  mtd2 rootfs   40 MiB  rootfs.squashfs
  mtd3 kernel2  5 MiB   xImage (A/B copy)
  mtd4 rootfs2  40 MiB  rootfs.squashfs (A/B copy)
  mtd5 ota      512 KiB "ota:kernel" padded-256 @0, theme "1" @0x20000
  mtd6 userdata rest    erased (UBIFS formats in-guest on first boot)

Raw page+OOB stream: 2112 B/page (2048 + 64), 64 pages/block, 1024 blocks.

Usage:
  tools/mk-nand-image.py --fw-dir DIR --out FILE [--userdata-from REF]
      DIR must contain xImage and rootfs.squashfs.
      --userdata-from REF copies REF's userdata partition (mtd6, raw
      page+OOB region) into the new image, so a rebuilt rootfs can be
      booted against a previously initialized /data state (A/B runs
      must start from identical userdata).
"""
import argparse
import os
import sys

PAGE = 2048
OOB = 64
STRIDE = PAGE + OOB
PAGES = 64 * 1024
TOTAL = STRIDE * PAGES  # 138,412,032

UBOOT_SZ = 2 * 1024 * 1024
KERNEL_SZ = 5 * 1024 * 1024
ROOTFS_SZ = 40 * 1024 * 1024
OTA_SZ = 512 * 1024

OFF_UBOOT = 0
OFF_KERNEL = OFF_UBOOT + UBOOT_SZ            # 0x00200000
OFF_ROOTFS = OFF_KERNEL + KERNEL_SZ          # 0x00700000
OFF_KERNEL2 = OFF_ROOTFS + ROOTFS_SZ         # 0x02f00000
OFF_ROOTFS2 = OFF_KERNEL2 + KERNEL_SZ        # 0x03400000
OFF_OTA = OFF_ROOTFS2 + ROOTFS_SZ            # 0x05c00000
OFF_USERDATA = OFF_OTA + OTA_SZ              # 0x05c80000


def poke(buf, nand_off, data):
    """Write `data` into the data-area stream, hopping over OOB gaps."""
    for i, b in enumerate(data):
        pos = nand_off + i
        page, col = divmod(pos, PAGE)
        buf[page * STRIDE + col] = b


def poke_file(buf, nand_off, path):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) > ROOTFS_SZ and "rootfs" in os.path.basename(path):
        sys.exit(f"error: {path} is {len(data)} bytes, mtd rootfs slot is "
                 f"{ROOTFS_SZ} — firmware too large")
    poke(buf, nand_off, data)
    print(f"  {os.path.basename(path):20s} -> {nand_off:#010x} "
          f"({len(data)} bytes)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fw-dir", required=True,
                    help="dir with xImage + rootfs.squashfs")
    ap.add_argument("--out", required=True, help="nand image to write")
    ap.add_argument("--userdata-from",
                    help="reference nand image whose mtd6 userdata region "
                         "is copied into the new image")
    args = ap.parse_args()

    fw = os.path.abspath(args.fw_dir)
    ximage = os.path.join(fw, "xImage")
    rootfs = os.path.join(fw, "rootfs.squashfs")
    for p in (ximage, rootfs):
        if not os.path.isfile(p):
            sys.exit(f"error: missing {p}")

    buf = bytearray(b"\xff" * TOTAL)
    poke_file(buf, OFF_KERNEL, ximage)
    poke_file(buf, OFF_ROOTFS, rootfs)
    poke_file(buf, OFF_KERNEL2, ximage)
    poke_file(buf, OFF_ROOTFS2, rootfs)

    # mtd5 ota: boot-target flag at 0 (padded to 256), theme id at 0x20000
    # (S11jpeg_display_shell compares the raw 7 bytes there).
    flag = b"ota:kernel"
    poke(buf, OFF_OTA, flag + b" " * (256 - len(flag)))
    poke(buf, OFF_OTA + 0x20000, b"theme:1")

    # Ingenic in-flash partition table at byte 0x5800 (driver default when
    # the DT has no partition-offset):
    #   header {u32 magic 'nand' (LE), u32 count}
    #   entries 48 B: {char name[0x20], u32 size @0x20, u32 offset @0x24,
    #                  u32 flags @0x28}
    parts = [
        (b"uboot",    UBOOT_SZ,   OFF_UBOOT),
        (b"kernel",   KERNEL_SZ,  OFF_KERNEL),
        (b"rootfs",   ROOTFS_SZ,  OFF_ROOTFS),
        (b"kernel2",  KERNEL_SZ,  OFF_KERNEL2),
        (b"rootfs2",  ROOTFS_SZ,  OFF_ROOTFS2),
        (b"ota",      OTA_SZ,     OFF_OTA),
        (b"userdata", 128 * 1024 * 1024 - OFF_USERDATA, OFF_USERDATA),
    ]
    hdr = b"nand" + len(parts).to_bytes(4, "little")
    poke(buf, 0x5800, hdr)
    for i, (name, size, off) in enumerate(parts):
        entry = name.ljust(0x20, b"\0") + size.to_bytes(4, "little") \
            + off.to_bytes(4, "little") + (0).to_bytes(4, "little")
        poke(buf, 0x5808 + i * 48, entry.ljust(48, b"\0"))
    print(f"  partition table      -> 0x00005800 ({len(parts)} entries)")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(buf)

    if args.userdata_from:
        # The file is a raw page+OOB stream: data offset D lives at
        # (D // PAGE) * STRIDE + D % PAGE. The userdata region starts on a
        # page boundary, so the whole file tail from that page copies
        # verbatim (data bytes + OOB gaps + erase counters).
        start = (OFF_USERDATA // PAGE) * STRIDE + OFF_USERDATA % PAGE
        with open(args.userdata_from, "rb") as f:
            f.seek(start)
            ref = f.read()
        if len(ref) != TOTAL - start:
            sys.exit(f"error: {args.userdata_from} userdata region is "
                     f"{len(ref)} bytes, expected {TOTAL - start}")
        with open(args.out, "r+b") as f:
            f.seek(start)
            f.write(ref)
        print(f"  userdata region      -> copied {len(ref)} bytes from "
              f"{args.userdata_from}")

    print(f"wrote {args.out} ({TOTAL} bytes, "
          f"{TOTAL // STRIDE} pages x {STRIDE} B)")


if __name__ == "__main__":
    main()
