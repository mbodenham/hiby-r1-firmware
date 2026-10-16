#!/usr/bin/env python3
"""Drive the guest console (ttyS2) of a QEMU x1600 boot over a unix
socket — scripted shell sessions for the plan's I/O tests (C1 write
latency A/B, power-cut recovery).

The inittab respawn shell (`console::respawn:-/bin/sh`) sits on
/dev/console once rcS finishes; the player's console output is silenced
(plan B5), so the prompt is reachable.

Command file directives (one per line):
  WAIT-BOOT      wait for the first shell prompt
  CMD <line>     run a shell command, wait for its completion marker
  SLEEP <sec>    idle
  KILL           SIGKILL the emulator (power cut)
  END            close cleanly (SIGTERM)

Usage:
  tools/qemu-console.py --fw-dir DIR --nand FILE --cmds FILE [--label L]
      [--timeout SEC] [--out DIR]
"""
import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = os.path.normpath(os.path.join(HERE, "..", "..", "hiby-r1-sdk"))
QEMU = os.path.join(SDK, "tools", "vendors", "qemu", "build",
                    "qemu-system-mipsel")
VMLINUX = os.path.join(SDK, "unpacked", "1.6", "vmlinux.bin")

PROMPT_RE = re.compile(r"(^|\r\n)\s*(\#|\$) ?$")


class Console:
    def __init__(self, sockpath, logpath):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(sockpath)
        self.s.setblocking(False)
        self.buf = b""
        self.log = open(logpath, "wb")

    def pump(self, timeout=0.5):
        self.s.settimeout(timeout)
        try:
            data = self.s.recv(65536)
            if data:
                self.buf += data
                self.log.write(data)
                self.log.flush()
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()
        except socket.timeout:
            pass
        except BlockingIOError:
            pass

    def wait_re(self, pattern, timeout):
        rx = re.compile(pattern)
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.5)
            m = rx.search(self.buf.decode("utf-8", "replace"))
            if m:
                return m
        raise TimeoutError(f"pattern not seen in {timeout}s: {pattern}")

    def wait_prompt(self, timeout):
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.5)
            m = PROMPT_RE.search(self.buf.decode("utf-8", "replace")[-2000:])
            if m:
                return True
        return False

    def send(self, line):
        self.s.sendall(line.encode() + b"\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fw-dir", required=True)
    ap.add_argument("--nand", required=True)
    ap.add_argument("--cmds", required=True)
    ap.add_argument("--label", default="console")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--qemu", default=QEMU)
    ap.add_argument("--vmlinux", default=VMLINUX)
    args = ap.parse_args()

    run = os.path.join("/tmp/kilo/console", f"{args.label}-{int(time.time())}")
    boot = os.path.join(run, "boot")
    os.makedirs(boot)
    sockpath = os.path.join(run, "console.sock")
    btlog = os.path.join(run, "bt.log")
    stderr = os.path.join(run, "stderr.log")

    os.symlink(os.path.abspath(args.vmlinux), os.path.join(boot, "vmlinux.bin"))
    os.symlink(os.path.abspath(args.nand), os.path.join(boot, "nand-r1.bin"))

    qemu = subprocess.Popen(
        [args.qemu, "-M", "x1600", "-kernel", os.path.join(boot, "vmlinux.bin"),
         "-serial", f"unix:{sockpath},server,nowait",
         "-serial", f"file:{btlog}",
         "-display", "none", "-monitor", "none", "-no-reboot"],
        stderr=open(stderr, "wb"), stdout=subprocess.DEVNULL)
    print(f"qemu pid {qemu.pid}, console socket {sockpath}", flush=True)

    # wait for the socket to accept
    con = None
    for _ in range(50):
        try:
            con = Console(sockpath, os.path.join(run, "session.log"))
            break
        except (ConnectionRefusedError, FileNotFoundError):
            time.sleep(0.2)
    if con is None:
        qemu.kill()
        sys.exit("cannot connect console socket")

    rc = 0
    try:
        with open(args.cmds) as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.rstrip("\n")
                if not line.strip() or line.startswith("#"):
                    continue
                if qemu.poll() is not None:
                    print(f"[qemu exited before directive {lineno}]")
                    rc = 1
                    break
                if line == "WAIT-BOOT":
                    ok = con.wait_prompt(args.timeout)
                    print(f"\n[prompt {'seen' if ok else 'NOT seen'}]")
                    if not ok:
                        rc = 1
                        break
                elif line.startswith("CMD "):
                    cmd = line[4:]
                    marker = f"M{lineno}-$?-DONE"
                    con.buf = b""
                    con.send(f"{cmd}; echo {marker}")
                    m = con.wait_re(re.escape(marker.split("-$?")[0]) +
                                    r"-(\d+)-DONE", 300)
                    print(f"\n[cmd rc={m.group(1)}]")
                elif line.startswith("SLEEP "):
                    t = float(line[6:])
                    end = time.time() + t
                    while time.time() < end:
                        con.pump(0.5)
                    print(f"\n[slept {t}s]")
                elif line == "KILL":
                    qemu.send_signal(signal.SIGKILL)
                    qemu.wait(timeout=10)
                    print("\n[qemu killed — power cut]")
                elif line == "END":
                    break
                else:
                    print(f"unknown directive: {line}")
                    rc = 1
    except TimeoutError as e:
        print(f"\n[TIMEOUT: {e}]")
        rc = 1
    finally:
        if qemu.poll() is None:
            qemu.send_signal(signal.SIGTERM)
            try:
                qemu.wait(timeout=10)
            except subprocess.TimeoutExpired:
                qemu.kill()
    sys.exit(rc)


if __name__ == "__main__":
    main()
