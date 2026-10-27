# -*- coding: utf-8 -*-
"""Tiny launcher used by the Explorer context menu (standard library only, so it starts in a few milliseconds).

Explorer starts one process per selected file. Every process drops its file into a queue; the first one becomes the
"leader", waits until no more files are arriving (so even hundreds of files end up in ONE window) and then starts
the real program once with the whole list.

Race-free: files are written atomically (.tmp + rename), picked up atomically (rename to a unique name before reading),
and the leader releases the lock first and only THEN picks up the queue once more - a process that wrote its file just
before its failed attempt to become the leader is therefore never lost."""
import os
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    args = sys.argv[1:]
    if not args:
        return
    flags, first = args[:-1], args[-1]
    qd = os.path.join(tempfile.gettempdir(), "KonwertexpolQueue")
    os.makedirs(qd, exist_ok=True)
    mine = uuid.uuid4().hex
    tmpf = os.path.join(qd, mine + ".tmp")
    with open(tmpf, "w", encoding="utf-8") as f:
        f.write(first)
    os.replace(tmpf, os.path.join(qd, mine + ".txt"))        # only now is the file visible to the leader

    lock = os.path.join(qd, "leader.lock")
    try:
        os.mkdir(lock)
    except FileExistsError:
        try:                                                  # a stale lock left after a crash
            if time.time() - os.stat(lock).st_mtime > 180:
                os.rmdir(lock)
                os.mkdir(lock)
            else:
                return                                        # somebody else is the leader - our file is queued
        except OSError:
            return

    def count():
        return sum(1 for n in os.listdir(qd) if n.endswith(".txt"))

    files = []

    def drain():
        for n in sorted(os.listdir(qd)):
            if not n.endswith(".txt"):
                continue
            p = os.path.join(qd, n)
            taken = p[:-4] + "." + uuid.uuid4().hex + ".taken"
            try:
                os.replace(p, taken)                          # atomic: only one process gets this file
            except OSError:
                continue
            try:
                with open(taken, encoding="utf-8") as fh:
                    files.append(fh.read().strip())
            except OSError:
                pass
            finally:
                try:
                    os.remove(taken)
                except OSError:
                    pass

    try:
        time.sleep(0.7)                                       # the first wave of processes
        last, t0 = -1, time.time()
        while time.time() - t0 < 180:                         # keep waiting while files are still arriving
            n = count()
            if n == last:
                break
            last = n
            time.sleep(0.5)
        drain()
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass
    drain()                                                   # whoever wrote a file before the lock was released

    files = sorted({f for f in files if f}, key=str.lower)
    if not files:
        return
    lst = os.path.join(tempfile.gettempdir(), f"konwertexpol_{uuid.uuid4().hex}.lst")
    with open(lst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(files))
    cmd = [sys.executable, os.path.join(HERE, "converter.py"), *flags, "--list", lst]
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200         # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, close_fds=True, **kw)


if __name__ == "__main__":
    main()
