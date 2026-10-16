#!/usr/bin/env python3
"""Boot a repacked HiBy R1 firmware in the vendored QEMU x1600 machine and
extract the Track A boot timeline (plan .kilo/plan.md A1).

The machine model loads nand-r1.bin from the -kernel file's directory, so
each run assembles a scratch boot dir: stock vmlinux.bin (symlink; the
kernel is untouched by this plan — src/xImage is byte-identical to stock)
+ the firmware's nand image (built with tools/mk-nand-image.py).

Serial0 = ttyS2 console (captured); serial1 = ttyS0 BT uart (discardable).
The rcS timeline comes from the guest-uptime stamps injected in
etc/init.d/rcS ("[rcS] <uptime> begin <script>") — guest uptime, not wall
time, because TCG wall time is meaningless.

Usage:
  tools/qemu-boot-ab.py --fw-dir out/somewhere --label m1-run1 \
      [--nand FILE] [--stop-kicks N] [--grace SEC] [--timeout SEC]
      [--keep-fresh] [--qemu PATH] [--vmlinux PATH]

--nand       reuse an existing nand image (A/B runs boot the same snapshot);
             default: build a fresh image in the run dir (first-boot shape).
--stop-kicks stop N guest fb kicks after the player exec stamp (default 6).
--grace      seconds to keep booting after the stop condition (default 45).
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = os.path.normpath(os.path.join(HERE, "..", "..", "hiby-r1-sdk"))
QEMU = os.path.join(SDK, "tools", "vendors", "qemu", "build",
                    "qemu-system-mipsel")
VMLINUX = os.path.join(SDK, "unpacked", "1.6", "vmlinux.bin")

RE_RCS = re.compile(r"\[rcS\] ([0-9.]+) (begin|rcS done)(?: (/\S+))?")
RE_PLAYER = re.compile(r"\[boot\] player exec ([0-9.]+)")
RE_KICK = re.compile(r"x1600_lcd: fb descriptor @")
RE_UBI_MOUNT = re.compile(r"UBIFS.*mounted UBI device")
RE_MOUNT_FAIL = re.compile(r"mount: mounting .* failed")
RE_HALTED = re.compile(r"reboot: System halted")


def tail(path, n=4000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 262144))
            return f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fw-dir", required=True,
                    help="dir with xImage + rootfs.squashfs")
    ap.add_argument("--label", required=True, help="run label")
    ap.add_argument("--nand", help="existing nand image to boot (A/B state)")
    ap.add_argument("--stop-kicks", type=int, default=6)
    ap.add_argument("--grace", type=int, default=45)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--qemu", default=QEMU)
    ap.add_argument("--vmlinux", default=VMLINUX)
    args = ap.parse_args()

    fw = os.path.abspath(args.fw_dir)
    for p in ("xImage", "rootfs.squashfs"):
        if not os.path.isfile(os.path.join(fw, p)):
            sys.exit(f"error: {fw}/{p} missing")

    run = os.path.join("/tmp/kilo/ab", f"{args.label}-{int(time.time())}")
    boot = os.path.join(run, "boot")
    os.makedirs(boot)
    console = os.path.join(run, "console.log")
    btlog = os.path.join(run, "bt.log")
    stderr = os.path.join(run, "stderr.log")

    os.symlink(os.path.abspath(args.vmlinux), os.path.join(boot, "vmlinux.bin"))

    if args.nand:
        nand = os.path.abspath(args.nand)
        if not os.path.isfile(nand):
            sys.exit(f"error: nand image not found: {nand}")
        os.symlink(nand, os.path.join(boot, "nand-r1.bin"))
    else:
        nand = os.path.join(run, "nand-r1.bin")
        subprocess.run([sys.executable, os.path.join(HERE, "mk-nand-image.py"),
                        "--fw-dir", fw, "--out", nand], check=True)
        os.symlink(nand, os.path.join(boot, "nand-r1.bin"))

    print(f"booting {args.label}: {fw} -> {run}", flush=True)
    t0 = time.time()
    with open(stderr, "wb") as errf:
        qemu = subprocess.Popen(
            [args.qemu, "-M", "x1600", "-kernel",
             os.path.join(boot, "vmlinux.bin"),
             "-serial", f"file:{console}", "-serial", f"file:{btlog}",
             "-display", "none", "-monitor", "none", "-no-reboot"],
            stderr=errf, stdout=subprocess.DEVNULL)

        player_uptime = None
        kicks_after_player = 0
        grace_left = None
        result = None
        try:
            while True:
                wall = time.time() - t0
                if qemu.poll() is not None:
                    result = "qemu-exited"
                    break
                if wall > args.timeout:
                    result = "timeout"
                    break
                text = tail(console)
                if RE_HALTED.search(text):
                    result = "halted (stock 5-min power-save shutdown)"
                    break
                if player_uptime is None:
                    m = RE_PLAYER.search(text)
                    if m:
                        player_uptime = float(m.group(1))
                        print(f"  player exec at guest {player_uptime}s "
                              f"(wall {wall:.0f}s)", flush=True)
                else:
                    kicks_after_player = sum(
                        1 for line in tail(stderr).splitlines()
                        if RE_KICK.search(line))
                    if grace_left is None and \
                            kicks_after_player >= args.stop_kicks:
                        grace_left = args.grace
                        print(f"  stop condition met: {kicks_after_player} "
                              f"player kicks (wall {wall:.0f}s)", flush=True)
                    if grace_left is not None:
                        grace_left -= 2
                        if grace_left <= 0:
                            result = "stopped"
                            break
                time.sleep(2)
        finally:
            qemu.send_signal(signal.SIGTERM)
            try:
                qemu.wait(timeout=10)
            except subprocess.TimeoutExpired:
                qemu.kill()

    wall = time.time() - t0
    console_text = tail(console, 10 ** 9)
    stderr_text = tail(stderr, 10 ** 9)

    timeline = [{"uptime": float(m.group(1)), "kind": m.group(2),
                 "script": (m.group(3) or "").strip()}
                for m in RE_RCS.finditer(console_text)]
    player_stamps = RE_PLAYER.findall(console_text)
    player_stamp = float(player_stamps[-1]) if player_stamps else None
    kicks = sum(1 for line in stderr_text.splitlines() if RE_KICK.search(line))
    ubi_mounted = bool(RE_UBI_MOUNT.search(console_text))
    mount_fail = bool(RE_MOUNT_FAIL.search(console_text))
    splash_done = "Finish display" in console_text

    out = {
        "label": args.label,
        "fw_dir": fw,
        "nand": nand if args.nand else "fresh",
        "stop": result,
        "wall_s": round(wall, 1),
        "player_exec_uptime": player_stamp,
        "fb_kicks_total": kicks,
        "fb_kicks_after_player": kicks_after_player,
        "splash_done": splash_done,
        "ubi_mounted": ubi_mounted,
        "mount_fail": mount_fail,
        "timeline": timeline,
        "run_dir": run,
    }
    with open(os.path.join(run, "result.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps({k: v for k, v in out.items() if k != "timeline"},
                     indent=2))
    print("timeline:")
    for t in timeline:
        print(f"  {t['uptime']:8.2f}  {t['kind']:9s} {t['script']}")
    if player_stamp is None:
        print("WARNING: player exec stamp not found", file=sys.stderr)
    if not splash_done:
        print("WARNING: splash not finished", file=sys.stderr)
    if not ubi_mounted or mount_fail:
        print("WARNING: /usr/data mount problem", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
