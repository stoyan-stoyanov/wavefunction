"""The boundary the agent's local tools work inside.

Two jobs are commonly confused here. Making a forbidden action *impossible* is this module's
job. Telling the user what is about to happen is the approval prompt's job, and it lives
elsewhere. The distinction matters because a prompt fails open — forget to ask and the thing
already happened — while a boundary fails closed. So nothing above this module is load-bearing
for safety, which is what lets the prompt be one plain sentence instead of an exhaustive system.

macOS enforcement is Seatbelt via `sandbox-exec`. It has carried a DEPRECATED banner since 2017
and every major browser and coding agent still depends on it; Apple's own OS does too. The real
risk is not removal but profile rot — a new macOS release makes some library read a sysctl the
profile does not allow, and everything breaks at once. `self_test` exists for exactly that.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import os
import pwd
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Hardcoded rather than found on PATH: an attacker who can prepend to PATH could otherwise
# supply their own `sandbox-exec` that enforces nothing.
SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# Where the folder grants live in app settings. Defined beside Grant so everything that
# reads or writes the stored list shares one name.
GRANTS_SETTING = "folder_grants"

# Built rather than inherited. The sidecar's own environment holds the token that unlocks the
# local API, and provider keys are read from the environment before the file, so passing it
# through would hand a shell command the keys to everything.
SAFE_ENVIRONMENT = ("HOME", "LANG", "LC_ALL", "TERM", "TZ", "USER")
# Preserve the bootloader's own values when launching the same frozen interpreter. Without
# these, every file helper unpacks the entire application again into its scratch folder.
FROZEN_ENVIRONMENT = ("_PYI_ARCHIVE_FILE", "_PYI_APPLICATION_HOME_DIR", "_PYI_PARENT_PROCESS_LEVEL")

# Credentials stay private even inside a granted folder. Task execution also restricts reads to
# folder/runtime grants. Relative to home.
PRIVATE_FOLDERS = (".ssh", ".gnupg", ".aws", "Library/Keychains")
# The installed app's data folder. Private even to a development backend, whose own data
# folder is elsewhere, so a checkout's agents cannot reach the real workspace's credentials.
INSTALLED_DATA_DIR = "Library/Application Support/app.harness.coding"

# The only launchd services a sandboxed command may look up: user and group lookups, and the
# per-user temporary folders the toolchain needs. LaunchServices, Apple events, the pasteboard
# and the window server are services too, so `open`, `osascript`, `pbcopy` and every app they
# would start fail however they are invoked — a renamed copy of `open` included. Modelled on
# the Codex CLI's Seatbelt policy.
MACH_SERVICES = (
    "com.apple.system.opendirectoryd.libinfo",
    "com.apple.system.opendirectoryd.membership",
    "com.apple.bsd.dirhelper",
)
# Added with network access: DNS and proxy configuration, and certificate trust for TLS. Not
# com.apple.SecurityServer, which Codex allows: it answers keychain searches.
NETWORK_SERVICES = (
    "com.apple.SystemConfiguration.DNSConfiguration",
    "com.apple.SystemConfiguration.configd",
    "com.apple.networkd",
    "com.apple.trustd.agent",
    "com.apple.ocspd",
)

# Appended to the user's own PATH: where Homebrew and the system keep their tools, for when a
# login shell does not say.
SYSTEM_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")

# Where package managers keep their caches in the sandbox, as folders of a writable cache
# folder. Installs and builds write caches even offline, and the user's own (~/.npm, ~/.cache,
# ~/Library/Caches, ~/go, ~/.cargo) are read-only there. rustup still finds its toolchains:
# they live in ~/.rustup, not in CARGO_HOME. Poetry keeps its virtualenvs in its cache.
TOOL_CACHES = {
    "XDG_CACHE_HOME": "xdg",
    "npm_config_cache": "npm",
    "PIP_CACHE_DIR": "pip",
    "UV_CACHE_DIR": "uv",
    "GOCACHE": "go-build",
    "GOMODCACHE": "go-mod",
    "CARGO_HOME": "cargo",
    "POETRY_CACHE_DIR": "poetry",
}

DEFAULT_TIMEOUT = 120
# How much of each output stream rides in the result. Beyond that the whole stream is kept in
# a file under the scratch space, and the result says where — `read_file` with offsets reads
# the rest. The 40,000-byte cap this replaced drained and discarded everything past it.
PREVIEW_BYTES = 40_000
# Spool files older than this are removed when a new one is written: they are the agent's
# own working files, and a day is longer than any turn that could still want one.
SPOOL_MAX_AGE = 24 * 3600


class SandboxUnavailable(RuntimeError):
    """Raised when the boundary cannot be enforced, so nothing should run."""


@dataclass(frozen=True)
class Grant:
    """One folder the user has opened up, and how far."""

    path: Path
    writable: bool = False

    @staticmethod
    def parse(entry: dict[str, object]) -> Grant:
        return Grant(
            # Resolved because Seatbelt matches literal paths: a rule written against /tmp
            # silently fails to match /private/tmp, and the deny it was guarding never applies.
            path=Path(str(entry.get("path", ""))).expanduser().resolve(),
            writable=bool(entry.get("writable", False)),
        )


def interpreter() -> Path | None:
    """Use the backend's interpreter, preserving the virtual-environment path.

    Resolving the symlink would bypass the private environment and lose its dependencies.
    Frozen development builds still use their executable's --python entry point.
    """
    return Path(sys.executable).absolute()


@functools.cache
def _user_folder(name: str) -> Path | None:
    """One of macOS's per-user folders, `DARWIN_USER_TEMP_DIR` or `DARWIN_USER_CACHE_DIR`.

    Command-line toolchains write to both before they will run — `xcrun`, behind `/usr/bin/git`
    and `clang`, keeps its cache in the temporary one — and the paths are per-user, so they are
    looked up rather than hardcoded.
    """
    try:
        found = subprocess.run(["/usr/bin/getconf", name], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    path = Path(found.stdout.strip())
    return path.resolve() if found.returncode == 0 and path.is_dir() else None


@functools.cache
def developer_runtime() -> Path | None:
    """The selected command-line tools or Xcode bundle, including its shared frameworks."""
    try:
        result = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    path = Path(result.stdout.strip())
    if result.returncode or not path.is_absolute() or not path.is_dir():
        return None
    path = path.resolve()
    return path.parent if path.parent.name == "Contents" and path.parent.parent.suffix == ".app" else path


def private_folders() -> list[Path]:
    """What no sandboxed command reads or changes: credentials, and the app's own data.

    The data folder in use holds the database, every agent's history and the credentials
    file; the installed app's is added so a development backend cannot open the real one.
    Resolved, because a rule for a symlinked folder would not match the files behind it.
    """
    from .config import get_settings

    home = Path.home()
    folders = [home / name for name in PRIVATE_FOLDERS]
    folders += [get_settings().data_dir, home / INSTALLED_DATA_DIR]
    return list(dict.fromkeys(folder.resolve() for folder in folders))


def holds_app_data(folder: Path, data_dir: Path) -> bool:
    """Whether a grant of `folder` would open the app's data: the data folder itself, a folder
    inside it (projects, skills, agents, the database), or one that contains it."""
    data_dir = data_dir.resolve()
    return folder == data_dir or data_dir in folder.parents or folder in data_dir.parents


@functools.cache
def login_path() -> tuple[str, ...]:
    """The PATH a new Terminal window would have.

    An app started from the Dock inherits almost none, so tools installed by Homebrew, cargo,
    go or a Node manager would not be found. Asked once, with a short timeout, of an interactive
    login shell, as Terminal starts: nvm, conda, pyenv and the like add themselves in `.zshrc`,
    which a non-interactive shell never reads. The inherited PATH if the shell does not answer.
    """
    shell = os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/zsh"
    marker = "__WAVEFUNCTION_PATH__"
    environment = {name: os.environ[name] for name in SAFE_ENVIRONMENT if name in os.environ}
    environment.update(SHELL=shell, PATH="/usr/bin:/bin:/usr/sbin:/sbin")
    try:
        found = subprocess.run(
            [shell, "-i", "-l", "-c", f'printf "{marker}%s{marker}" "$PATH"'],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
            env=environment,
        )
        # Split on markers: a profile that prints a greeting must not become part of PATH.
        path = found.stdout.split(marker)[1] if found.stdout.count(marker) >= 2 else ""
    except (OSError, subprocess.SubprocessError):
        path = ""
    return tuple(entry for entry in (path or os.environ.get("PATH", "")).split(":") if entry)


def command_path() -> list[str]:
    """The user's own PATH, then the usual system folders, without repeats."""
    return list(dict.fromkeys([*login_path(), *SYSTEM_PATH]))


_GIT_PATH_SETTING = re.compile(
    r"^\s*(path|excludesfile|attributesfile|hookspath)\s*=\s*(.+?)\s*$", re.IGNORECASE
)


def git_config_files() -> list[Path]:
    """The user's global Git configuration, which git reads in every repository: the config
    files, the files they include, the global ignore and attributes files, and the global hooks
    folder, whose hooks `git commit` runs (a hook it can see but not read fails the commit). Each both as
    written and resolved, since a dotfile manager's symlink is checked at its target; git
    treats an unreadable include or global config as fatal."""
    home = Path.home()
    pending = [home / ".gitconfig", home / ".config/git/config"]
    found = [home / ".config/git/ignore", home / ".config/git/attributes"]
    seen: set[Path] = set()
    while pending and len(seen) < 32:
        config = pending.pop()
        if config in seen:
            continue
        seen.add(config)
        found.append(config)
        try:
            text = config.read_text(errors="replace")
        except OSError:
            continue
        section = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                section = (stripped[1:].split("]")[0].split() or [""])[0].split('"')[0].lower()
                continue
            if not (match := _GIT_PATH_SETTING.match(line)):
                continue
            value = match.group(2)
            # A quoted value is taken whole; an unquoted one ends at an inline comment.
            value = (
                value[1:].split('"')[0] if value.startswith('"') else re.split(r"\s[#;]", value)[0].strip()
            )
            key, path = match.group(1).lower(), Path(value).expanduser()
            if key == "path" and section in {"include", "includeif"}:
                pending += (
                    [path] if path.is_absolute() else [config.parent / path, config.resolve().parent / path]
                )
            elif (
                section == "core"
                and key in {"excludesfile", "attributesfile", "hookspath"}
                and path.is_absolute()
            ):
                found.append(path)
    return list(dict.fromkeys(entry for path in found for entry in (path, path.resolve())))


def toolchain_roots() -> list[Path]:
    """Read-only runtime support, without granting home, a folder in it or one that holds it."""
    home = Path.home()
    roots = [
        home / path
        for path in (
            ".nvm/versions",
            ".fnm/node-versions",
            ".local/share/fnm/node-versions",
            ".rustup/toolchains",
            ".rustup/settings.toml",
            ".cargo/bin",
            ".pyenv/versions",
            ".asdf/installs",
            ".volta/tools",
            ".local/share/mise/installs",
            ".local/share/uv",
            ".local/share/pipx/venvs",
            ".bun/bin",
            # Managers whose shims run code kept beside them (rbenv's libexec, pnpm's store),
            # conda installs and their environments, and JDKs that /usr/bin/java finds.
            ".rbenv",
            ".nodenv",
            "Library/pnpm",
            "miniconda3",
            "anaconda3",
            "miniforge3",
            "opt/miniconda3",
            "opt/anaconda3",
            ".conda",
            "Library/Java/JavaVirtualMachines",
        )
    ]
    # Go's official installer, JDKs installed from a .pkg, and conda installed for all users.
    roots += [
        Path(path)
        for path in (
            "/usr/local/go",
            "/Library/Java/JavaVirtualMachines",
            "/opt/miniconda3",
            "/opt/anaconda3",
            "/opt/miniforge3",
        )
    ]
    # Where a PATH bin's install folder is never granted: home and its folders, which can hold
    # credentials beside a bin (~/.cargo, ~/.docker); the system and Homebrew prefixes, whose
    # folders the profile lists one by one; and a volume's root.
    home_root = home.resolve()
    prefixes = {Path(path).parent for path in SYSTEM_PATH}
    for entry in command_path():
        directory = Path(entry).expanduser()
        if not directory.is_absolute() or directory.name not in {"bin", "sbin", "shims"}:
            continue
        if str(directory) in SYSTEM_PATH:
            continue  # These system/Homebrew prefixes are already covered by the profile.
        roots.append(directory)
        # A toolchain installed elsewhere, such as Go's /usr/local/go or a conda in /opt, runs
        # from its bin but reads the libraries and sources beside it.
        install = directory.resolve().parent
        if directory.is_dir() and not (
            install.is_relative_to(home_root) or install in prefixes or os.path.ismount(install)
        ):
            roots.append(install)
        # uv/pipx and other installers expose symlinks to an executable elsewhere.
        with contextlib.suppress(OSError):
            for path in directory.iterdir():
                if path.is_symlink() and path.is_file() and os.access(path, os.X_OK):
                    target = path.resolve()
                    roots.append(target)
                    # Node CLIs also load their package.json, libraries and bundled
                    # dependencies. Permit that package, not the surrounding prefix.
                    package = next(
                        (p for p in (target.parent, target.parent.parent) if (p / "package.json").is_file()),
                        None,
                    )
                    if package is not None:
                        roots.append(package)
    return list(
        dict.fromkeys(
            path.resolve()
            for path in roots
            if path.exists() and not home.resolve().is_relative_to(path.resolve())
        )
    )


def _quote(path: Path | str) -> str:
    """Escape a path for the profile language.

    Paths reach here from a folder picker, but they are still user data being spliced into a
    policy, and a quote or backslash in a folder name would otherwise end the string early and
    let the rest be read as rules.
    """
    escaped = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# What commands with network access may use of the app's GitHub proxy (github_proxy): its Unix
# socket, and the folder of gh's configuration that names it, read-only.
LOCAL_SOCKETS: set[str] = set()
READABLE_FOLDERS: set[str] = set()


def build_profile(
    grants: list[Grant],
    *,
    scratch: Path,
    network: bool = False,
    tty: Path | None = None,
    private_roots: list[Path] | None = None,
    restrict_reads: bool = False,
) -> str:
    """Render the policy for one command.

    Deny-by-default. Task execution restricts reads to folder grants and explicit runtime
    support; without `restrict_reads` (the startup self-check) everything but the private
    folders is readable. Writes stay inside writable grants, scratch and tool caches. The
    network is closed unless enabled.
    """
    from .repository_search import ripgrep_binary

    private = (
        "(require-any "
        + " ".join(f"(subpath {_quote(p)})" for p in [*private_folders(), *(private_roots or [])])
        + ")"
    )
    services = MACH_SERVICES + (NETWORK_SERVICES if network else ())
    lines = [
        "(version 1)",
        "(deny default)",
        *(
            [f'(allow file-read* file-write* file-ioctl (literal {_quote(tty)}) (literal "/dev/tty"))']
            if tty
            else []
        ),
        # Start programs, and signal or inspect only those this command started. `process*`
        # also reached every other process on the Mac.
        "(allow process-exec process-fork)",
        "(allow signal process-info* (target same-sandbox))",
        "(allow sysctl*)",
        "(allow mach-lookup " + " ".join(f"(global-name {_quote(s)})" for s in services) + ")",
        # The frozen interpreter's loader takes a SysV semaphore before it unpacks itself, so
        # allowing only the POSIX kind stops it starting at all.
        "(allow ipc-posix-sem)",
        "(allow ipc-posix-shm)",
        "(allow ipc-sysv-sem)",
        "(allow ipc-sysv-shm)",
        "(allow file-read-metadata)",
        *([] if restrict_reads else [f"(allow file-read* (require-not {private}))"]),
        # Read and written by `>/dev/null` and `cmd &`, which the shell points at /dev/null.
        '(allow file-read-data file-write-data (require-all (path "/dev/null") (vnode-type CHARACTER-DEVICE)))',
        # Random bytes and zeros: `head -c 16 /dev/urandom`, `dd if=/dev/zero`, Ruby's SecureRandom.
        '(allow file-read-data (require-all (require-any (literal "/dev/urandom") (literal "/dev/random") (literal "/dev/zero")) (vnode-type CHARACTER-DEVICE)))',
        '(allow file-read* file-write* (subpath "/dev/fd"))',
        # Somewhere to put intermediate files without asking: this agent's scratch space, and
        # the per-user temporary and cache folders the toolchain writes before it will run.
        f"(allow file-read* file-write* (subpath {_quote(scratch)}))",
        *(
            f"(allow file-read* file-write* (require-all (subpath {_quote(folder)}) (require-not {private})))"
            for name in ("DARWIN_USER_TEMP_DIR", "DARWIN_USER_CACHE_DIR")
            if not restrict_reads and (folder := _user_folder(name)) is not None
        ),
        # The app's own interpreter, which the installed app keeps inside its private data.
        *(
            [f"(allow file-read* (subpath {_quote(binary.parent)}))"]
            if (binary := interpreter()) is not None
            else []
        ),
        *(
            [
                f"(allow file-read* (subpath {_quote(Path(prefix).resolve())}))"
                for prefix in {sys.prefix, sys.base_prefix}
            ]
            if not getattr(sys, "frozen", False)
            else []
        ),
        f"(allow file-read* (literal {_quote(ripgrep_binary())}))",
        # The GitHub CLI the app installed, which it keeps inside its private data.
        *([f"(allow file-read* (literal {_quote(gh)}))"] if (gh := managed_gh()) else []),
    ]

    if restrict_reads:
        # macOS process startup/getcwd needs the root directory itself, not its contents.
        lines.append('(allow file-read* (literal "/"))')
        lines.append(f"(allow file-read* (literal {_quote(Path(__file__).resolve().parent.parent)}))")
        # Executables, libraries and tool configuration are runtime support, not arbitrary
        # user folders. Project/user data is readable only through explicit folder grants.
        runtime_roots = [
            Path(p)
            for p in (
                "/System",
                "/usr/bin",
                "/usr/sbin",
                "/usr/lib",
                "/usr/libexec",
                "/usr/share",
                "/bin",
                "/sbin",
                "/Library/Developer",
                "/Library/Apple",
                "/Library/Frameworks",
                "/private/etc",
                # What /etc/localtime, /usr/share/zoneinfo and /etc/resolv.conf point to: without
                # them every command runs on UTC and Ruby's net/http cannot load. And the system
                # Ruby's own gems, without which /usr/bin/ruby cannot start.
                "/private/var/db/timezone",
                "/private/var/run/resolv.conf",
                "/Library/Ruby",
            )
        ]
        runtime_roots += [
            Path("/opt/homebrew") / p for p in ("bin", "sbin", "Cellar", "opt", "lib", "share", "etc")
        ]
        runtime_roots += [
            Path("/usr/local") / p for p in ("bin", "sbin", "Cellar", "opt", "lib", "share", "etc")
        ]
        runtime_roots += [Path(__file__).resolve().parent, *toolchain_roots()]
        if getattr(sys, "frozen", False):
            # The loader sets this directory; an environment variable must never grant access.
            runtime_roots.append(Path(sys._MEIPASS).resolve())  # pyright: ignore[reportAttributeAccessIssue]
        if developer := developer_runtime():
            runtime_roots.append(developer)
        for root in runtime_roots:
            lines.append(f"(allow file-read* (require-all (subpath {_quote(root)}) (require-not {private})))")
        # Cold xcrun lookups launch xcodebuild, which checks the accepted-license state.
        lines.append(
            f"(allow file-read* (literal {_quote(Path('/Library/Preferences/com.apple.dt.Xcode.plist'))}))"
        )
        for path in git_config_files():
            # A subpath: for a file, the file alone; for the hooks folder, its hooks.
            lines.append(f"(allow file-read* (require-all (subpath {_quote(path)}) (require-not {private})))")
        cache = (_user_folder("DARWIN_USER_CACHE_DIR") or scratch) / "wavefunction-tools"
        lines.append(f"(allow file-read* file-write* (subpath {_quote(cache)}))")
        if temporary := _user_folder("DARWIN_USER_TEMP_DIR"):
            # xcrun ignores TMPDIR for its database and randomized replacement files.
            # Permit only those files, never the rest of the user's temporary data.
            pattern = "^" + re.escape(str(temporary / "xcrun_db")) + "[^/]*$"
            lines.append(
                f"(allow file-read* file-write* (require-all (regex #{_quote(pattern)}) (require-not {private})))"
            )

    for grant in grants:
        if restrict_reads:
            lines.append(
                f"(allow file-read* (require-all (subpath {_quote(grant.path)}) (require-not {private})))"
            )
        if grant.writable:
            lines.append(
                f"(allow file-write* (require-all (subpath {_quote(grant.path)}) (require-not {private})))"
            )

    if network:
        # Out to any address. The local sockets are the DNS resolver's and the app's own for gh
        # (github_proxy): others, such as Docker's, would hand the command something outside the
        # sandbox. Inbound cannot be limited to this Mac: Seatbelt checks listen, not each
        # connection, names hosts only as "localhost" or "*", and its "localhost" is every address
        # of this Mac, so a server on all interfaces is reachable from the network (KNOWN_ISSUES).
        sockets = "".join(
            f" (remote unix-socket (path-literal {_quote(Path(path))}))" for path in sorted(LOCAL_SOCKETS)
        )
        lines.append(
            "(allow network-outbound (remote ip) "
            f'(remote unix-socket (path-literal "/private/var/run/mDNSResponder")){sockets})'
        )
        lines.extend(
            f"(allow file-read* (subpath {_quote(Path(folder))}))" for folder in sorted(READABLE_FOLDERS)
        )
        # Listening and binding each have their own check. "localhost" already reached any of this
        # Mac's addresses, 0.0.0.0 included, but matching it now and then refused a loopback bind
        # or listen while the Mac's network was settling (8 of 8,000 sandboxed binds on a starting
        # CI runner; none with "*"). A local address is all either can name, so "*" is the same set.
        lines.append('(allow network-inbound (local ip "*:*"))')
        lines.append('(allow network-bind (local ip "*:*"))')
    else:
        # A grant opens a folder, never a socket — otherwise anything the command reads could
        # leave the machine.
        lines.append("(deny network*)")

    return "\n".join(lines) + "\n"


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    # Where each complete stream was kept when it outgrew the preview, else None.
    stdout_file: Path | None = None
    stderr_file: Path | None = None


async def _collect(
    stream: asyncio.StreamReader,
    spool_dir: Path,
    name: str,
    spools: list[Path],
    *,
    tail: bool = False,
    on_output: Callable[[str, bytes], None] | None = None,
) -> tuple[bytes, Path | None, int]:
    """Read a stream to its end: the first PREVIEW_BYTES in memory, all of it on disk once it
    outgrows that.

    `communicate()` would hold everything a command prints for the whole timeout, and 900
    seconds of `yes` is a memory hole; the spool file is what keeps the output instead.
    """
    preview = bytearray()
    spool: Path | None = None
    handle = None
    total = 0
    try:
        while chunk := await stream.read(65536):
            total += len(chunk)
            if handle is None and total > PREVIEW_BYTES:
                spool = _new_spool(spool_dir, name)
                spools.append(spool)
                handle = spool.open("wb")
                handle.write(bytes(preview))
            if handle is not None:
                handle.write(chunk)
            if tail:
                preview.extend(chunk)
                del preview[:-PREVIEW_BYTES]
            else:
                preview.extend(chunk[: max(0, PREVIEW_BYTES - len(preview))])
            if on_output:
                on_output(name, bytes(preview))
    finally:
        if handle is not None:
            handle.close()
    return bytes(preview), spool, total


def _new_spool(spool_dir: Path, name: str) -> Path:
    spool_dir.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - SPOOL_MAX_AGE
    for old in spool_dir.iterdir():
        with contextlib.suppress(OSError):
            if old.stat().st_mtime < cutoff:
                old.unlink()
    return spool_dir / f"{uuid.uuid4().hex}.{name}"


def _text(preview: bytes, spool: Path | None, total: int, *, tail: bool = False) -> str:
    # A tail can begin inside a UTF-8 code point. Skip only those leading continuation bytes.
    if tail and spool:
        while preview and preview[0] & 0xC0 == 0x80:
            preview = preview[1:]
    text = preview.decode(errors="replace")
    if spool is None:
        return text
    if tail:
        return (
            f"[Showing the last {len(preview):,} of {total:,} bytes. Complete output: "
            f"read_file(path={str(spool)!r}, offset=0).]\n{text}"
        )
    return (
        f"{text}\n[Output continues: {total:,} bytes in total. The complete output is in "
        f"{spool}; read the rest with read_file(path={str(spool)!r}, offset={len(text)}).]"
    )


def _end_tagged(command_id: str) -> None:
    """Stop what is left of one command: this user's processes whose environment carries its tag.

    Killing the process group misses a descendant that started a session of its own (`setsid`,
    `start_new_session`), which would otherwise outlive the command. Such a descendant still
    inherited the environment, and `ps -E` shows it. Where `ps` cannot show environments this
    stops nothing, and the group kill is all there is.
    """
    try:
        listed = subprocess.run(
            ["/bin/ps", "-Eww", "-o", "pid=,command=", "-U", str(os.getuid())],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return
    tag = f"WAVEFUNCTION_COMMAND_ID={command_id}"
    for line in listed.splitlines():
        pid, _, rest = line.strip().partition(" ")
        if tag in rest and pid.isdigit():
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(int(pid), signal.SIGKILL)


def _discard(spools: list[Path]) -> None:
    """A command that was stopped has no reader for its output; leave nothing behind."""
    for spool in spools:
        spool.unlink(missing_ok=True)


async def _run_process(
    args: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    timeout: float,
    scratch: Path,
    tail: bool = False,
    on_output: Callable[[str, str], None] | None = None,
    stdin: bytes | None = None,
    scan_detached: bool = True,
) -> CommandResult:
    """Run one command to the end, spooling its output, and stop it if it overruns.

    Shared by both runners. What differs between them — the boundary, the environment, the
    working directory — is decided by the caller; starting, collecting and stopping a
    process is the same either way, and used to be written out twice. `scan_detached=False`
    is for the app's own helpers, which start nothing that outlives them: they skip the
    process scan that finds a command's detached descendants.
    """
    # Tags everything the command starts, so what escapes its process group can still be found.
    command_id = uuid.uuid4().hex
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # Closed rather than inherited: sandbox rules are checked when a file is opened, so a
        # descriptor opened before the boundary applied would stay usable inside it. The only
        # input a command gets is a request's own bytes, on a pipe, never the command line.
        stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
        env={**env, "WAVEFUNCTION_COMMAND_ID": command_id},
        cwd=str(cwd),
        # Its own process group, so stopping stops everything. Killing only /bin/sh leaves a
        # foreground brew or docker build running — still changing the Mac after the UI said
        # the work stopped, which is the one lie a full-access runner must not tell.
        start_new_session=True,
    )
    if stdin is not None and process.stdin is not None:
        # Buffered by the pipe's transport, written as the command reads, then closed.
        process.stdin.write(stdin)
        process.stdin.close()
    spools: list[Path] = []

    async def end_group() -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        if scan_detached:
            await asyncio.to_thread(_end_tagged, command_id)

    async def stop() -> None:
        await end_group()
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(1):
                await process.wait()
        # A detached descendant may keep inherited pipes open after the shell exits.
        # asyncio exposes no public Process.close(); close its transport so collectors
        # receive EOF and retain their partial output without waiting for that child.
        process._transport.close()  # pyright: ignore[reportAttributeAccessIssue]
        await process.wait()

    # Snapshots are bounded and throttled, so a verbose build cannot flood the event stream.
    recent = {"stdout": "", "stderr": ""}
    loop = asyncio.get_running_loop()
    pending = None
    last_update = 0.0
    dirty = False

    def flush_output():
        nonlocal pending, last_update, dirty
        pending = None
        if dirty and on_output:
            dirty = False
            last_update = loop.time()
            on_output(recent["stdout"], recent["stderr"])

    def changed(name, data):
        nonlocal pending, dirty
        recent[name] = data[-4000:].decode(errors="replace")
        dirty = True
        if pending is None:
            delay = max(0, 0.25 - (loop.time() - last_update))
            pending = loop.call_later(delay, flush_output)

    spool_dir = scratch / "output"
    tasks = [
        asyncio.create_task(
            _collect(
                process.stdout,  # pyright: ignore[reportArgumentType] (a pipe, never None)
                spool_dir,
                "stdout",
                spools,
                tail=tail,
                on_output=changed if on_output else None,
            )
        ),
        asyncio.create_task(
            _collect(
                process.stderr,  # pyright: ignore[reportArgumentType] (a pipe, never None)
                spool_dir,
                "stderr",
                spools,
                tail=tail,
                on_output=changed if on_output else None,
            )
        ),
        asyncio.create_task(process.wait()),
    ]
    collectors = asyncio.gather(*tasks)
    timed_out = False
    try:
        try:
            async with asyncio.timeout(timeout):
                # Until the command returns. `process.wait()` also waits for the output pipes to
                # close, which a background child can hold open, so the exit status is watched.
                while process.returncode is None and not collectors.done():
                    await asyncio.wait([collectors], timeout=0.05)
                if collectors.done():
                    collectors.result()  # Output that could not be kept fails the command now.
                # The command has returned, and what it started in the background ends with it:
                # a server left behind by `&` would keep changing the Mac, and holding the
                # output pipes, after the tool reported the command finished.
                await end_group()
                await asyncio.shield(collectors)
        except TimeoutError:
            timed_out = True
            await stop()
        # Killing the process closes its pipes; drain their remaining bytes on timeout.
        (out, out_file, out_total), (err, err_file, err_total), _ = await collectors
    except (asyncio.CancelledError, Exception):
        # The user pressed Stop, or a spool could not be written (a full disk, most likely).
        # Either way the command must not be left running after the tool has reported
        # failure — least of all an unrestricted one still changing the Mac.
        await stop()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await collectors
        _discard(spools)
        raise
    finally:
        if pending is not None:
            pending.cancel()
    flush_output()

    return CommandResult(
        124 if timed_out else process.returncode or 0,
        _text(out, out_file, out_total, tail=tail),
        _text(err, err_file, err_total, tail=tail) + (f"\nTimed out after {timeout:g}s" if timed_out else ""),
        timed_out=timed_out,
        stdout_file=out_file,
        stderr_file=err_file,
    )


def managed_gh() -> Path | None:
    """The GitHub CLI the app installed for its sign-in (local_integrations), when the user has
    none of their own on PATH: commands use it through a shim, as they do the bundled rg."""
    from .config import get_settings

    if any((Path(folder) / "gh").is_file() for folder in command_path() if Path(folder).is_absolute()):
        return None
    managed = get_settings().data_dir / "integrations" / "bin" / "gh"
    return managed.resolve() if managed.is_file() else None


def _write_shim(scratch: Path, name: str, text: str) -> None:
    """A script in the task's bin folder. The command can change that folder, so a link it left
    in place of the script, or of the folder, is removed rather than written through."""
    bin_dir = scratch / "bin"
    if bin_dir.is_symlink():
        bin_dir.unlink()
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / name
    with contextlib.suppress(FileNotFoundError):
        shim.unlink()
    handle = os.open(shim, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
    with os.fdopen(handle, "w") as out:
        out.write(text)


def _install_tools(scratch: Path) -> None:
    from .repository_search import ripgrep_binary

    _write_shim(scratch, "rg", f'#!/bin/sh\nexec {shlex.quote(str(ripgrep_binary()))} "$@"\n')
    if gh := managed_gh():
        _write_shim(scratch, "gh", f'#!/bin/sh\nexec {shlex.quote(str(gh))} "$@"\n')
    else:
        # The user has their own gh now. The shim for the copy the app installed would come first
        # on PATH and run a file the profile no longer lets it read. `bin` is a real folder here:
        # writing the rg shim replaced any link left in its place.
        with contextlib.suppress(FileNotFoundError):
            (scratch / "bin" / "gh").unlink()


class Sandbox:
    """Runs one command inside the boundary, with the network only when `network` is set."""

    def __init__(
        self,
        grants: list[Grant],
        *,
        scratch: Path,
        network: bool = False,
        private_roots: list[Path] | None = None,
        restrict_reads: bool = False,
        env: dict[str, str] | None = None,
    ):
        self.grants = grants
        self.scratch = scratch.resolve()
        self.network = network
        self.private_roots = [path.resolve() for path in private_roots or []]
        self.restrict_reads = restrict_reads
        # Variables for this command alone, such as the GitHub sign-in (github_cli).
        self.env = dict(env or {})

    @staticmethod
    def available() -> bool:
        return os.path.exists(SANDBOX_EXEC)

    def environment(self) -> dict[str, str]:
        built = {name: os.environ[name] for name in SAFE_ENVIRONMENT if name in os.environ}
        if getattr(sys, "frozen", False):
            built.update({name: os.environ[name] for name in FROZEN_ENVIRONMENT if name in os.environ})
        # The shims first, so `python3` is the project's own environment or the app's; then
        # the tools the user would find in Terminal. The profile, not PATH, is the boundary.
        built["PATH"] = ":".join(dict.fromkeys([str(self.scratch / "bin"), *command_path()]))
        built["TMPDIR"] = str(self.scratch)
        caches = (_user_folder("DARWIN_USER_CACHE_DIR") or self.scratch) / "wavefunction-tools"
        built.update({name: str(caches / folder) for name, folder in TOOL_CACHES.items()})
        # Swift and clang build modules (`import Foundation`) into this cache and stop when they
        # cannot write it. Built from each project's own sources, so in this task's scratch,
        # never the cache shared by every build on the Mac.
        built["CLANG_MODULE_CACHE_PATH"] = str(self.scratch / "clang-modules")
        _default_locale(built)
        built.update(self.env)
        return built

    def _install_interpreter(self) -> None:
        """Put a `python3` on the sandbox's PATH: the project's `.venv` when the command runs
        where one is, otherwise the app's own interpreter.

        Written as a shim rather than pointed at directly so an ordinary `python3 script.py`
        works in a shell the model wrote, without it needing to know anything about how this
        app is packaged. The project's environment wins because it has the dependencies and
        test runner the code expects. The app's interpreter runs without `-I`, which would
        drop the script's folder (or, for `-m` and `-c`, the working folder) from the import
        path, so `python3 -m unittest` and a script importing its neighbours both work.
        """
        _install_tools(self.scratch)
        binary = interpreter()
        if binary is None:
            return
        mode = "--python" if getattr(sys, "frozen", False) else "-E -s"
        for name in ("python", "python3"):
            _write_shim(
                self.scratch,
                name,
                "#!/bin/sh\n"
                'if [ -x "$PWD/.venv/bin/python" ]; then exec "$PWD/.venv/bin/python" "$@"; fi\n'
                f'exec {shlex.quote(str(binary))} {mode} "$@"\n',
            )

    def prepare_launch(self, argv: list[str], *, tty: Path | None = None) -> tuple[list[str], dict[str, str]]:
        if not self.available():
            raise SandboxUnavailable("This Mac has no sandbox available, so commands cannot be run safely.")
        self.scratch.mkdir(parents=True, exist_ok=True)
        self._install_interpreter()
        profile = build_profile(
            self.grants,
            scratch=self.scratch,
            network=self.network,
            tty=tty,
            private_roots=self.private_roots,
            restrict_reads=self.restrict_reads,
        )
        return [SANDBOX_EXEC, "-p", profile, *argv], self.environment()

    async def run(
        self,
        command: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        cwd: Path | None = None,
        tail: bool = False,
        on_output: Callable[[str, str], None] | None = None,
        stdin: bytes | None = None,
        scan_detached: bool = True,
    ) -> CommandResult:
        argv, env = self.prepare_launch(["/bin/sh", "-c", command])
        return await _run_process(
            argv,
            env=env,
            cwd=cwd or self.scratch,
            timeout=timeout,
            scratch=self.scratch,
            tail=tail,
            on_output=on_output,
            stdin=stdin,
            scan_detached=scan_detached,
        )


def _default_locale(env: dict[str, str]) -> None:
    """UTF-8, as in Terminal and the app's own terminals. Opened from Finder or the Dock, the app
    has no locale, and tools such as CocoaPods then read every file as ASCII."""
    if not any(env.get(name) for name in ("LANG", "LC_ALL", "LC_CTYPE")):
        env["LANG"] = "en_US.UTF-8"


async def run_outside(
    command: str,
    *,
    cwd: Path,
    scratch: Path,
    timeout: float = DEFAULT_TIMEOUT,
    on_output: Callable[[str, str], None] | None = None,
) -> CommandResult:
    """Run a command the user allowed outside the sandbox, as in their own terminal: with their
    environment and login PATH, so Docker, the Keychain and SSH keys work. The app's own variables
    are left out: they hold the token that unlocks its API."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("_PYI_", "_MEI", "WAVEFUNCTION_"))
    }
    env["PATH"] = ":".join(command_path())
    _default_locale(env)
    scratch.mkdir(parents=True, exist_ok=True)
    return await _run_process(
        ["/bin/sh", "-c", command],
        env=env,
        cwd=cwd,
        timeout=timeout,
        scratch=scratch,
        tail=True,
        on_output=on_output,
    )


async def self_test(scratch: Path) -> str | None:
    """Prove the boundary is real. Returns a reason when it is not.

    Run at startup because the failure is silent otherwise: a profile that stops being accepted
    after a macOS update, or a `sandbox-exec` that quietly does nothing, both look exactly like
    a working sandbox until something escapes. Claude Code's profile broke this way on Tahoe.
    """
    if not Sandbox.available():
        return "sandbox-exec is missing from this Mac"

    from .config import get_settings

    sandbox = Sandbox([], scratch=scratch)
    # The app's data folder always exists and is readable to the user, so listing it succeeds
    # exactly when the private folders are not private.
    data = shlex.quote(str(get_settings().data_dir.resolve()))
    try:
        denied = await sandbox.run(
            f"ls -a {data} 2>/dev/null || cat ~/.ssh/known_hosts 2>/dev/null", timeout=15
        )
    except Exception as error:  # noqa: BLE001 - any failure here means unproven, so unsafe
        return f"the sandbox could not be started: {error}"
    if denied.exit_code == 0 and denied.stdout.strip():
        return "the sandbox allowed a read it should have denied"

    allowed = await sandbox.run("echo ok", timeout=15)
    if allowed.exit_code != 0 or allowed.stdout.strip() != "ok":
        return f"the sandbox blocked a command it should have allowed: {allowed.stderr.strip()}"

    return None


def load_grants(raw: object) -> list[Grant]:
    if not isinstance(raw, list):
        return []
    grants: list[Grant] = []
    for entry in raw:
        if isinstance(entry, dict) and str(entry.get("path", "")).strip():
            grants.append(Grant.parse(entry))
    return grants


def scratch_for(root: Path) -> Path:
    # Resolved, because the policy matches literal paths and /tmp is a symlink to /private/tmp.
    # An unresolved path here means the rule never matches and the command has nowhere to work.
    return (root / ".scratch").resolve()


__all__ = [
    "Grant",
    "Sandbox",
    "SandboxUnavailable",
    "build_profile",
    "holds_app_data",
    "load_grants",
    "private_folders",
    "scratch_for",
    "self_test",
]
