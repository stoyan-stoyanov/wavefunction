"""The Linux boundary: Bubblewrap for what a command can see, seccomp for what it can reach.

Bubblewrap starts each command in new namespaces with an empty root, and mounts into it only what
the policy allows, read-only unless the policy says otherwise. A private folder inside a granted
one is covered by an empty read-only mount, which Landlock could not do: its rules only add access.

The network namespace is shared when a command has network access, and a shared one also shares
abstract Unix sockets (X11's, D-Bus's, containerd's), which no mount can hide. A socket file such as
Docker's is reachable through any mount that shows it, read-only included. So a seccomp filter
refuses to create Unix sockets at all, as Seatbelt refuses to connect to them on macOS.
`socketpair` stays allowed for streams, whose ends reach only each other; a datagram end can be
connected anywhere afterwards, so those are refused too.

Fail closed: without `/usr/bin/bwrap`, or on an architecture the filter does not know, nothing runs.
"""

from __future__ import annotations

import contextlib
import errno
import os
import platform
import struct
import sys
import uuid
from pathlib import Path

# Hardcoded for the reason sandbox.py hardcodes sandbox-exec: a `bwrap` earlier on PATH could
# enforce nothing.
BWRAP = "/usr/bin/bwrap"
# The descriptor bwrap reads the filter from. The launcher opens it from a file in the app's data
# folder, which commands cannot see, so a command cannot swap the filter its successors get.
FILTER_FD = 3
# What commands read to run at all. /etc includes the resolver, certificate and user files.
RUNTIME = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc", "/nix/store")

# Classic BPF, as `struct sock_filter`, and the seccomp return values.
_LOAD, _JEQ, _JGE, _AND, _RETURN = 0x20, 0x15, 0x35, 0x54, 0x06
_ALLOW = 0x7FFF0000
_ERRNO = 0x00050000
# Where `struct seccomp_data` keeps the syscall number, the architecture and the low half of each
# argument (both architectures here are little-endian).
_NR, _ARCH = 0, 4
_ARG = (16, 24)
AF_UNIX = 1
# A socket's type, without the SOCK_CLOEXEC and SOCK_NONBLOCK flags, and the two kinds whose ends
# stay connected to each other for good.
SOCK_TYPE_MASK, SOCK_STREAM, SOCK_SEQPACKET = 0xF, 1, 5
TIOCSTI, TIOCLINUX = 0x5412, 0x541C
# x86-64's x32 ABI shares the architecture value and flags its syscall numbers with this bit.
X32_SYSCALL_BIT = 0x40000000
ARCHITECTURES = {
    # AUDIT_ARCH_*, socket, socketpair, ioctl. io_uring_setup, _enter and _register are 425 to 427
    # on both.
    "x86_64": (0xC000003E, 41, 53, 16),
    "aarch64": (0xC00000B7, 198, 199, 29),
}
IO_URING = (425, 426, 427)


def available() -> bool:
    return os.path.exists(BWRAP) and _architecture() in ARCHITECTURES


def _architecture() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)


def seccomp_filter() -> bytes:
    """The compiled filter for this machine's architecture.

    Refused with EPERM: creating a Unix socket, or a pair of them other than a stream or
    sequenced-packet one; TIOCSTI and TIOCLINUX, which type into a terminal;
    and every syscall of another ABI (i386 and x32 on x86-64), whose numbers differ. io_uring can
    create sockets without the `socket` syscall, so it answers ENOSYS and libraries fall back.
    The kernel reads only the low 32 bits of `socket`'s domain and `ioctl`'s request.
    """
    audit, socket, socketpair, ioctl = ARCHITECTURES[_architecture()]
    # Instructions as (code, value, label if true, label if false); a string marks a label.
    program: list[tuple[int, int, str | None, str | None] | str] = [
        (_LOAD, _ARCH, None, None),
        (_JEQ, audit, None, "deny"),
        (_LOAD, _NR, None, None),
        (_JGE, X32_SYSCALL_BIT, "deny", None),
        (_JEQ, socket, "socket", None),
        (_JEQ, socketpair, "socketpair", None),
        (_JEQ, ioctl, "ioctl", None),
        *((_JEQ, number, "unsupported", None) for number in IO_URING),
        (_RETURN, _ALLOW, None, None),
        "socket",
        (_LOAD, _ARG[0], None, None),
        (_JEQ, AF_UNIX, "deny", None),
        (_RETURN, _ALLOW, None, None),
        "socketpair",
        (_LOAD, _ARG[1], None, None),
        (_AND, SOCK_TYPE_MASK, None, None),
        (_JEQ, SOCK_STREAM, "allow", None),
        (_JEQ, SOCK_SEQPACKET, "allow", "deny"),
        "ioctl",
        (_LOAD, _ARG[1], None, None),
        (_JEQ, TIOCSTI, "deny", None),
        (_JEQ, TIOCLINUX, "deny", None),
        "allow",
        (_RETURN, _ALLOW, None, None),
        "deny",
        (_RETURN, _ERRNO | errno.EPERM, None, None),
        "unsupported",
        (_RETURN, _ERRNO | errno.ENOSYS, None, None),
    ]
    labels: dict[str, int] = {}
    instructions: list[tuple[int, int, str | None, str | None]] = []
    for entry in program:
        if isinstance(entry, str):
            labels[entry] = len(instructions)
        else:
            instructions.append(entry)

    def jump(index: int, label: str | None) -> int:
        # Relative to the next instruction; None falls through.
        return 0 if label is None else labels[label] - index - 1

    return b"".join(
        struct.pack("<HBBI", code, jump(index, true), jump(index, false), value)
        for index, (code, value, true, false) in enumerate(instructions)
    )


def filter_file(data_dir: Path) -> Path:
    """The filter, written once into the app's data folder, which commands cannot see or change."""
    contents = seccomp_filter()
    folder = data_dir / "sandbox"
    path = folder / f"seccomp-{_architecture()}.bpf"
    with contextlib.suppress(OSError):
        if path.read_bytes() == contents:
            return path
    folder.mkdir(parents=True, exist_ok=True)
    # Named for this write alone: two commands can start at once.
    temporary = folder / f".seccomp-{uuid.uuid4().hex}.tmp"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(handle, "wb") as out:
        out.write(contents)
    os.replace(temporary, path)
    return path


def _bind(source: Path, *, writable: bool = False) -> list[str]:
    """Mount a path at the same place. A link in a system root (merged /usr: /bin -> usr/bin)
    stays a link, so it leads where it does outside."""
    if source.is_symlink() and str(source) in RUNTIME:
        return ["--symlink", os.readlink(source), str(source)]
    return ["--bind" if writable else "--ro-bind", str(source), str(source)]


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def symlinks(path: Path) -> list[tuple[str, Path]]:
    """The links met on the way to `path`, as (target, link), in order. A virtual environment's
    `python` can lead through one in a folder the sandbox doesn't mount, such as uv's link from a
    minor version to the patch version installed."""
    found: list[tuple[str, Path]] = []
    current = Path(os.path.abspath(path))
    while len(found) < 40:
        for depth in range(2, len(current.parts) + 1):
            prefix = Path(*current.parts[:depth])
            if prefix.is_symlink():
                target = os.readlink(prefix)
                found.append((target, prefix))
                current = Path(os.path.normpath(prefix.parent / target)).joinpath(*current.parts[depth:])
                break
        else:
            break
    return found


def _scratch_folder(folder: Path, writable: list[Path]) -> list[str]:
    """A folder where only some files may change, as Seatbelt allows a file by its path: git's
    common folder for a worktree, where `packed-refs` is granted but `config` and `hooks` are not.

    Git changes a file by creating `name.lock` beside it and renaming that over it, which a mount
    cannot allow for one name. So the folder becomes a throwaway one, with each entry mounted back
    (writable where granted): locks come and go, nothing created there outlives the command, and
    a rename over a granted file fails ("Device or resource busy") rather than being lost.
    """
    mounts = ["--tmpfs", str(folder)]
    for entry in sorted(folder.iterdir()):
        if entry.is_symlink():
            mounts += ["--symlink", os.readlink(entry), str(entry)]
        else:
            mounts += _bind(entry, writable=_inside(entry, writable))
    return mounts


def launch(
    argv: list[str],
    *,
    filter_path: Path,
    readable: list[Path],
    writable: list[Path],
    private: list[Path],
    exempt: list[tuple[Path, bool]],
    links: list[Path],
    network: bool,
    restrict_reads: bool,
) -> list[str]:
    """The command line that runs `argv` inside the boundary.

    As in the Seatbelt profile, a path may be written when any writable grant holds it, and read
    when any grant or runtime root does. Mounted over the system (or, without `restrict_reads`,
    the whole disk) read-only: the grants, parents before children, read-only ones only where no
    writable grant holds them; then each private folder that shows by now, covered by an empty
    one; then the `exempt` paths, which the policy allows even inside a private folder (path,
    writable); then the links on the way to each of `links` that nothing mounted shows; and
    last the covers and the root made read-only.
    """
    mounts: list[str] = []
    visible: list[Path] = []
    if restrict_reads:
        for root in RUNTIME:
            path = Path(root)
            if path.exists() or path.is_symlink():
                mounts += _bind(path)
                visible.append(path)
        # The resolver file is usually a link into /run, which is not mounted: Docker's socket
        # lives there.
        resolver = Path("/etc/resolv.conf").resolve()
        if resolver.is_file() and not _inside(resolver, visible):
            mounts += ["--ro-bind", str(resolver), str(resolver)]
    else:
        mounts += ["--ro-bind", "/", "/"]
        visible.append(Path("/"))
    # A fresh /dev with only the harmless devices and a new pseudo-terminal instance (a terminal's
    # own is inherited open), and a /proc of this command's processes.
    mounts += ["--dev", "/dev", "--proc", "/proc"]

    private = list(dict.fromkeys(private))
    writable = [path for path in dict.fromkeys(writable) if path.exists() or path.parent.is_dir()]
    folders = [path for path in writable if path.is_dir()]
    # (path, kind): 0 read-only, 1 a folder where only granted files change, 2 writable.
    plan = [(path, 0) for path in dict.fromkeys(readable) if path.exists() and not _inside(path, folders)]
    plan += [
        (path.parent, 1)
        for path in dict.fromkeys(writable)
        if not path.is_dir() and not _inside(path.parent, folders)
    ]
    plan += [(path, 2) for path in writable if path.exists()]
    # Private wins, as in the profile: a grant inside a private folder opens nothing.
    plan = [(path, kind) for path, kind in plan if not _inside(path, private)]
    for path, kind in sorted(dict.fromkeys(plan), key=lambda item: (len(item[0].parts), item[1])):
        mounts += _scratch_folder(path, writable) if kind == 1 else _bind(path, writable=kind == 2)
        visible.append(path)

    covers = [path for path in private if path.is_dir() and _inside(path, visible)]
    exempt = [(path, rule) for path, rule in dict.fromkeys(exempt) if path.exists()]
    for path in covers:
        # Opened by nothing, as Seatbelt answers: "Permission denied". A cover the exempt paths are
        # mounted through can be passed through, but still not listed.
        through = any(path in inner.parents for inner, _ in exempt)
        mounts += ["--perms", "0111" if through else "0000", "--tmpfs", str(path)]
    for path, rule in exempt:
        mounts += _bind(path, writable=rule)
    shown = [*visible, *(path for path, _ in exempt)]
    for target, link in dict.fromkeys(pair for path in links for pair in symlinks(path)):
        if not _inside(link, shown):
            mounts += ["--symlink", target, str(link)]
    # The covers, and the root, which bwrap leaves writable: a write anywhere not mounted writable
    # would otherwise seem to succeed and vanish with the command.
    for path in [*covers, Path("/")]:
        mounts += ["--remount-ro", str(path)]
    return [
        # Opens the filter on the descriptor bwrap reads it from, then becomes bwrap.
        "/bin/sh",
        "-c",
        f'filter=$1; shift; exec "$@" {FILTER_FD}<"$filter"',
        "sh",
        str(filter_path),
        BWRAP,
        "--unshare-all",
        *(["--share-net"] if network else []),
        "--die-with-parent",
        *mounts,
        "--seccomp",
        str(FILTER_FD),
        "--",
        *argv,
    ]


def prepare_shared_git(common: Path) -> None:
    """Make what a command may write in a managed worktree's shared Git folder before it is
    granted. This sandbox mounts only what exists, and keeps what a command creates in that folder
    for the command alone. So the granted folders are made first, or Git LFS objects and reflogs
    would vanish with the command; and `packed-refs` too, or a `pack-refs` would prune loose refs
    into a file that is then gone. Empty, each is what git and git-lfs read when there is none.
    Seatbelt grants by path, so nothing is needed on macOS."""
    if sys.platform != "linux":
        return
    for name in ("lfs", "logs/refs"):
        (common / name).mkdir(parents=True, exist_ok=True)
    if not (common / "packed-refs").exists():
        (common / "packed-refs").touch()


def left_out(path: str | None) -> bool:
    """Whether this sandbox did not mount `path`, which Seatbelt would have refused: then its
    nearest folder that exists is part of the sandbox's own empty root, where folders exist only
    to mount others through. Without restricted reads the root is the disk's, and nothing is."""
    if not path or sys.platform != "linux":
        return False
    with contextlib.suppress(OSError):
        with open("/proc/self/mountinfo") as mounts:
            root = [line for line in mounts if line.split()[4] == "/"][-1]
        if root.split(" - ")[1].split()[0] != "tmpfs":
            return False
        # Followed, so a link to a file the sandbox doesn't show counts as that file.
        folder = Path(os.path.realpath(path))
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        return os.stat(folder).st_dev == os.stat("/").st_dev
    return False


def refused_write(error: OSError) -> bool:
    """Whether a write failed as this sandbox refuses one: what it shows but may not change is
    mounted read-only. Seatbelt's refusal is a permission error instead."""
    return sys.platform == "linux" and error.errno == errno.EROFS


def unavailable_reason() -> str | None:
    """Why commands cannot run here, or None when the pieces are in place."""
    if not os.path.exists(BWRAP):
        return f"Bubblewrap is not installed ({BWRAP} is missing). Install the bubblewrap package."
    if _architecture() not in ARCHITECTURES:
        return f"the Linux sandbox does not support {_architecture()} yet"
    return None


__all__ = ["BWRAP", "available", "filter_file", "launch", "seccomp_filter", "unavailable_reason"]
