"""Small exec trampoline: acquire the inherited PTY before entering Seatbelt.

Separate from preexec_fn, which is unsafe in a multithreaded API process. Also works with
the frozen sidecar's --python module runner.
"""

import fcntl
import os
import sys
import termios


def main():
    ready = int(sys.argv[1])
    argv = sys.argv[2:]
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    # The frozen bootloader has a supervising parent. Report the actual session
    # leader before exec so the server owns the shell's session, not that wrapper.
    os.write(ready, f"{os.getpid()}\n".encode())
    os.close(ready)
    os.execvpe(argv[0], argv, os.environ)


if __name__ == "__main__":
    main()
