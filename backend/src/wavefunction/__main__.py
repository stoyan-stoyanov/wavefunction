from __future__ import annotations

import os
import signal
import threading
import time
from contextlib import suppress

# Every sandboxed file operation starts this program as an interpreter, so what it needs only as
# the server is imported only there.
SERVER_OPTIONS = ("--host", "--port", "--token", "--data-dir", "--parent-pid", "--log-level")


def parse_args():
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Wavefunction local desktop backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--log-level", default="warning")
    return parser.parse_args()


def _parent_is_alive(parent_pid: int) -> bool:
    if parent_pid <= 1:
        return False
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _watch_parent(parent_pid: int) -> None:
    while _parent_is_alive(parent_pid) and os.getppid() != 1:
        time.sleep(0.5)
    os.kill(os.getpid(), signal.SIGTERM)


def run_as_python() -> None:
    """Behave as an interpreter for the arguments after `--python`.

    The app carries a bundled Python runtime whose libraries are importable by this
    process and invisible to any other. Re-entering it here is what gives the sandbox
    an interpreter that always exists and always has them — the Mac's own `python3` is a shim
    that needs developer tools installed, and has none of these libraries anyway.
    """
    import runpy
    import sys

    rest = sys.argv[2:]
    isolated = False
    # The interpreter's own options before the program, as `python3 -u script.py` passes them.
    while rest and rest[0].startswith("-") and rest[0] not in ("-", "-c", "-m"):
        option = rest.pop(0)
        if option in ("-V", "--version"):
            print(f"Python {sys.version.split()[0]}")
            return
        if option.startswith("-W"):
            import warnings

            # As a real interpreter applies `-W error` or `-W ignore::DeprecationWarning`.
            value = option[2:] or (rest.pop(0) if rest else "")
            sys.warnoptions.append(value)
            warnings._processoptions([value])  # pyright: ignore[reportAttributeAccessIssue]
            continue
        if option == "-X":
            rest = rest[1:]  # Its value; implementation options do not apply to a running interpreter.
            continue
        if option.startswith(("-X", "--")):
            continue
        for index, flag in enumerate(option[1:], start=2):
            if flag in "cm":
                # `-uc code` or `-Im package`: the program ends the group, and the rest of the
                # group, if any, is its argument (`-cprint(1)`).
                rest[:0] = [f"-{flag}", *([option[index:]] if option[index:] else [])]
                break
            if flag == "I":
                # As for a real interpreter: nothing in the working folder is importable. The
                # app's own helper scripts run that way, so a repository's `json.py` cannot
                # stand in for theirs.
                isolated = True
            elif flag == "u":
                import io

                # Unbuffered all the way down, so output arrives as it is written.
                sys.stdout, sys.stderr = (
                    io.TextIOWrapper(
                        io.FileIO(stream.fileno(), "w", closefd=False),
                        encoding=stream.encoding,
                        errors=stream.errors,
                        write_through=True,
                    )
                    for stream in (sys.stdout, sys.stderr)
                )
            elif flag == "B":
                sys.dont_write_bytecode = True
            elif flag == "O":
                # The bundled interpreter's optimization level is fixed; running the program
                # with assertions it asked to drop would be different code.
                raise SystemExit("--python cannot honour -O; run without it")
            elif flag not in "EsSqbd":
                raise SystemExit(f"--python does not support the option -{flag}")
    sys.argv = ["python", *rest]
    import builtins

    if not hasattr(builtins, "exit"):
        import site

        # `exit()`, `quit()` and `help()`, which `site` adds to any interpreter but this one.
        site.setquit()
        site.sethelper()

    def run_main(source: str | bytes, filename: str, **names) -> None:
        # Run as the `__main__` module, as a real interpreter does, so the program's own
        # classes pickle and `unittest.main()` finds its tests.
        import types

        main = types.ModuleType("__main__")
        main.__dict__.update(names)
        sys.modules["__main__"] = main
        exec(compile(source, filename, "exec"), main.__dict__)  # noqa: S102

    if not rest or rest[0] == "-":
        # The working folder is importable, as in any interpreter session that is not isolated.
        if not isolated:
            sys.path.insert(0, "")
        if not rest and sys.stdin.isatty():
            import code

            code.interact(exitmsg="")
            return
        # A program on standard input, as `python3 -` or `python3 < script.py` runs it.
        sys.argv = ["-", *rest[1:]]
        run_main(sys.stdin.read(), "<stdin>", __file__="<stdin>")
        return

    def search_first(folder: str) -> None:
        # Where a real interpreter's import path starts: the working folder for -c and -m, the
        # script's own folder otherwise — so tests and a script's sibling modules import.
        if not isolated:
            sys.path.insert(0, folder)

    if rest[0] == "-c":
        if len(rest) < 2:
            raise SystemExit("--python -c needs code to run")
        sys.argv = ["-c", *rest[2:]]
        search_first("")
        run_main(rest[1], "<string>")
        return
    if rest[0] == "-m":
        if len(rest) < 2:
            raise SystemExit("--python -m needs a module to run")
        if rest[1] in ("venv", "ensurepip"):
            raise SystemExit(
                "The app's own python3 cannot create virtual environments. Use `uv venv`, "
                "or a Python installed on this Mac (for example `brew install python`)."
            )
        sys.argv = [rest[1], *rest[2:]]
        search_first(os.getcwd())
        runpy.run_module(rest[1], run_name="__main__", alter_sys=True)
        return
    sys.argv = rest
    from pathlib import Path

    script = os.path.abspath(rest[0])
    search_first(os.path.dirname(script))
    source = Path(script).read_bytes() if os.path.isfile(script) else b"\0"
    if b"\0" not in source:
        # Source: `__file__` absolute, as in Python 3.9 and later, and `sys.argv[0]` as typed.
        run_main(source, script, __file__=script, __cached__=None)
    else:
        runpy.run_path(script, run_name="__main__")  # Compiled code, an archive, or a folder.


def main() -> None:
    import multiprocessing
    import sys

    # In the packaged app, worker processes that Python's multiprocessing starts run this same
    # executable: this lets them do their work instead of starting a server.
    multiprocessing.freeze_support()
    # Checked before argument parsing: in this mode every remaining argument belongs to the
    # script, not to the server. As `sys.executable` of the agent's `python3`, this binary is
    # also started as an interpreter without the flag (`subprocess.run([sys.executable, ...])`):
    # anything but the server's own options is for the interpreter.
    if len(sys.argv) == 1 or sys.argv[1].partition("=")[0] not in SERVER_OPTIONS:
        if sys.argv[1:2] != ["--python"]:
            sys.argv.insert(1, "--python")
        try:
            run_as_python()
        finally:
            # A program may close its output, as `json.tool` does; the frozen app's loader flushes
            # it afterwards and would print a traceback for that.
            for name in ("stdout", "__stdout__", "stderr", "__stderr__"):
                stream = getattr(sys, name)
                if stream is None or getattr(stream, "closed", False):
                    setattr(sys, name, open(os.devnull, "w"))  # noqa: SIM115 - lives until exit
        return

    args = parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["WAVEFUNCTION_DATA_DIR"] = str(args.data_dir)
    os.environ["WAVEFUNCTION_PORT"] = str(args.port)
    os.environ["WAVEFUNCTION_DESKTOP_TOKEN"] = args.token
    os.environ["WAVEFUNCTION_DEVELOPMENT_AUTH_BYPASS"] = "false"

    if args.parent_pid is not None:
        threading.Thread(
            target=_watch_parent, args=(args.parent_pid,), name="wavefunction-parent-watch", daemon=True
        ).start()

    import asyncio

    import uvicorn

    # Import only after the process-specific settings are present: main constructs the local
    # repository and service graph at import time.
    from wavefunction import main as backend

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:
            super().handle_exit(sig, frame)
            # The desktop shell keeps a notifications request waiting. Answer it now, from the
            # server's loop rather than this signal handler, instead of having it cut off below.
            with suppress(RuntimeError):
                asyncio.get_running_loop().call_soon_threadsafe(backend.services.task_news.close)

    Server(
        uvicorn.Config(
            backend.app,
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            access_log=False,
            # On SIGTERM, a request still open after a second is cut off so the app's own shutdown
            # — which stops runs and their commands — starts inside the grace the desktop allows.
            timeout_graceful_shutdown=1,
        )
    ).run()


if __name__ == "__main__":
    main()
