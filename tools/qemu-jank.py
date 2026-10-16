#!/usr/bin/env python3
"""A3 jank harness: boot a firmware in QEMU x1600 with VNC + HMP monitor,
wait for the player UI, then drive scroll gestures through VNC pointer
events (the machine maps them to cst8xx touch) while timestamping the
LCD kick tap lines ("x1600_lcd: fb descriptor @") on QEMU stderr.

Metric per gesture window: fb-kick count and inter-kick intervals
(frame-advance rate). Compare baseline vs changed firmware with identical
gesture scripts and identical userdata state.

Usage:
  tools/qemu-jank.py --fw-dir DIR --nand FILE --label L
      [--gestures N] [--out DIR] [--shot] [--timeout SEC]

--shot takes one HMP screendump right after the UI is up (recon for
gesture coordinates) and exits.
"""
import argparse
import base64
import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = os.path.normpath(os.path.join(HERE, "..", "..", "hiby-r1-sdk"))
QEMU = os.path.join(SDK, "tools", "vendors", "qemu", "build",
                    "qemu-system-mipsel")
VMLINUX = os.path.join(SDK, "unpacked", "1.6", "vmlinux.bin")

RE_KICK = re.compile(r"x1600_lcd: fb descriptor @")
RE_PLAYER = re.compile(r"\[boot\] player exec ([0-9.]+)")
W, H = 480, 800


class Vnc:
    def __init__(self, port):
        deadline = time.time() + 30
        while True:
            try:
                self.s = socket.create_connection(("127.0.0.1", port),
                                                  timeout=10)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)
        self.s.settimeout(10)
        peer = self.s.recv(12)
        assert peer.startswith(b"RFB "), peer
        self.s.sendall(b"RFB 003.008\n")
        n = self.s.recv(1)[0]
        types = self.s.recv(n)
        assert b"\x01" in types[:1] or 1 in types, types  # None security
        self.s.sendall(bytes([1]))
        if n == 1:
            self.s.recv(4)  # SecurityResult (3.8)
        self.s.sendall(bytes([1]))          # ClientInit: shared
        init = self._recv(24)
        fw, fh = struct.unpack(">HH", init[0:4])
        assert (fw, fh) == (W, H), (fw, fh)
        # SetPixelFormat/SetEncodings are optional; pointer events only.
        self.s.setblocking(False)

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            d = self.s.recv(n - len(buf))
            if not d:
                raise ConnectionError("vnc closed")
            buf += d
        return buf

    def pointer(self, down, x, y):
        self.s.sendall(struct.pack(">BBHH", 1 if down else 0, 0,
                                   int(x), int(y)))

    def drain(self):
        try:
            while True:
                d = self.s.recv(65536)
                if not d:
                    break
        except BlockingIOError:
            pass


class Hmp:
    def __init__(self, path):
        for _ in range(100):
            try:
                self.s = socket.create_connection_unix = None
                self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.s.connect(path)
                break
            except (ConnectionRefusedError, FileNotFoundError):
                time.sleep(0.2)
        else:
            raise ConnectionError(path)
        self.s.settimeout(10)
        self._read_until(b"(qemu)")

    def _read_until(self, pat):
        buf = b""
        self.s.settimeout(10)
        while pat not in buf:
            d = self.s.recv(4096)
            if not d:
                break
            buf += d
        return buf

    def cmd(self, c):
        self.s.sendall(c.encode() + b"\n")
        time.sleep(0.3)
        try:
            return self._read_until(b"(qemu)")
        except socket.timeout:
            return b""


def tail_lines(path, keep_last=1 << 22):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - keep_last))
            return f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return ""


def new_kicks(stderr_text, seen):
    ks = [m.start() for m in RE_KICK.finditer(stderr_text)]
    out = []
    for pos in ks[seen:]:
        out.append(pos)
    return ks, out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fw-dir", required=True)
    ap.add_argument("--nand", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--gestures", type=int, default=10)
    ap.add_argument("--shot", action="store_true")
    ap.add_argument("--pre-tap", default="",
                    help='taps to send once the UI is up, "x,y;x,y"')
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--qemu", default=QEMU)
    ap.add_argument("--vmlinux", default=VMLINUX)
    args = ap.parse_args()

    run = os.path.join("/tmp/kilo/jank", f"{args.label}-{int(time.time())}")
    boot = os.path.join(run, "boot")
    os.makedirs(boot)
    console = os.path.join(run, "console.log")
    btlog = os.path.join(run, "bt.log")
    stderr = os.path.join(run, "stderr.log")
    vncport = 5910 + (os.getpid() % 80)

    os.symlink(os.path.abspath(args.vmlinux), os.path.join(boot, "vmlinux.bin"))
    os.symlink(os.path.abspath(args.nand), os.path.join(boot, "nand-r1.bin"))

    qemu = subprocess.Popen(
        [args.qemu, "-M", "x1600", "-kernel", os.path.join(boot, "vmlinux.bin"),
         "-serial", f"file:{console}", "-serial", f"file:{btlog}",
         "-display", "none", "-vnc", f"127.0.0.1:{vncport - 5900}",
         "-monitor", f"unix:{run}/mon.sock,server,nowait", "-no-reboot"],
        stderr=open(stderr, "wb"), stdout=subprocess.DEVNULL)
    print(f"{args.label}: qemu pid {qemu.pid} vnc :{vncport} -> {run}",
          flush=True)

    kicks_seen = 0
    player_at = None
    try:
        t0 = time.time()
        while time.time() - t0 < args.timeout:
            if qemu.poll() is not None:
                sys.exit("qemu exited early")
            text = tail_lines(console)
            if player_at is None:
                m = RE_PLAYER.search(text)
                if m:
                    player_at = time.time()
                    print(f"  player exec (wall {time.time()-t0:.0f}s)")
            else:
                ks, _ = new_kicks(tail_lines(stderr), 0)
                if len(ks) >= 6:
                    break
            time.sleep(1)
        else:
            sys.exit("timeout waiting for UI")

        vnc = Vnc(vncport)
        hmp = Hmp(os.path.join(run, "mon.sock"))
        for tap in [t for t in args.pre_tap.split(";") if t]:
            x, y = (int(v) for v in tap.split(","))
            vnc.pointer(True, x, y)
            time.sleep(0.15)
            vnc.pointer(False, x, y)
            print(f"  pre-tap {x},{y}")
            time.sleep(1.5)
        if args.shot:
            hmp.cmd('screendump "%s/ui.ppm"' % run)
            print(f"screendump: {run}/ui.ppm")
            return

        # let the first frame settle, then gesture loop:
        # press, drag up in steps, release — a list scroll
        results = []
        for g in range(args.gestures):
            vnc.drain()
            with open(stderr, "rb") as f:
                f.seek(0, 2)
                start_off = f.tell()
            start_wall = time.time()
            vnc.pointer(True, 240, 620)
            for i in range(12):
                vnc.pointer(True, 240, 620 - i * 34)
                time.sleep(0.03)
            vnc.pointer(False, 240, 620 - 11 * 34)
            # tail stderr for kick lines appended during the gesture
            deadline = time.time() + 4.0
            kicks = 0
            while time.time() < deadline:
                time.sleep(0.05)
                size = os.path.getsize(stderr)
                if size > start_off:
                    with open(stderr, "rb") as f:
                        f.seek(start_off)
                        chunk = f.read().decode("utf-8", "replace")
                    start_off = size
                    kicks += len(RE_KICK.findall(chunk))
                if time.time() - start_wall > 2.0 and kicks == 0:
                    continue
            results.append({"gesture": g, "kicks": kicks,
                            "wall_s": round(time.time() - start_wall, 3)})
            print(f"  gesture {g}: {kicks} kicks "
                  f"({results[-1]['wall_s']}s)", flush=True)
            time.sleep(1.0)

        out = {"label": args.label, "gestures": results,
               "total_kicks": sum(r["kicks"] for r in results)}
        with open(os.path.join(run, "jank.json"), "w") as f:
            json.dump(out, f, indent=2)
        print(json.dumps(out, indent=2))
    finally:
        if qemu.poll() is None:
            qemu.send_signal(signal.SIGTERM)
            try:
                qemu.wait(timeout=10)
            except subprocess.TimeoutExpired:
                qemu.kill()


if __name__ == "__main__":
    main()
