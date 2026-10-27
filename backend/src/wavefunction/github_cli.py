"""GitHub through the user's own GitHub CLI, as Hermes Agent, Claude Code, Codex, OpenCode and Pi
do it: `git` and `gh` in commands, and no GitHub MCP server, whose tools duplicate gh's.

The sign-in is gh's, on this Mac: made with GitHub's device flow in the setup card, and kept by gh
in the user's keychain, where their terminal uses it too.

Sandboxed commands cannot reach the Keychain or SSH keys, and are never given the token: they reach
GitHub through the app's proxy (github_proxy), which adds it on the way out. Wavefunction saves
none of it: not in a task's spec, a command's environment, Git configuration or a log.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

from .connectors import SignInRequired
from .local_integrations import executable, run_cli

HOST = "github.com"
# "First copy your one-time code: ABCD-1234", or "One-time code (ABCD-1234) copied to clipboard"
# when the user has gh's clipboard setting on.
DEVICE_CODE = re.compile(r"one-time code\W*([A-Z0-9]{4}-[A-Z0-9]{4})", re.IGNORECASE)
SIGNED_IN = re.compile(r"Logged in to github\.com (?:account|as) ([A-Za-z0-9-]+)")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# `gh auth status --active` arrived in 2.40.
OLDEST = (2, 40)


async def _gh(root: Path | None) -> str:
    # Finding gh asks the user's login shell for its PATH, once; not on the event loop.
    return await asyncio.to_thread(executable, "gh", root)


async def token(root: Path | None = None) -> str:
    """The token of the account gh is signed in to, asked of gh by the app. Never saved."""
    if root is None:
        from .config import get_settings

        root = get_settings().data_dir / "integrations"
    try:
        found = (await run_cli([await _gh(root), "auth", "token", "--hostname", HOST], timeout=20)).strip()
    except (ValueError, OSError):
        found = ""
    if not re.fullmatch(r"[\w.-]+", found):
        # Not gh's words: whatever it printed stays out of errors and logs.
        raise SignInRequired("the GitHub CLI on this Mac is not signed in to GitHub")
    return found


# gh is asked again after this many seconds, sooner when signed out, and at once when its
# account changes.
TOKEN_SECONDS, SIGNED_OUT_SECONDS = 60, 10
_command_token: tuple[float, int | None, str] | None = None
_token_lock_state: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None


def _gh_accounts_stamp() -> int | None:
    """When gh last changed its accounts: `gh auth switch`, login and logout all rewrite hosts.yml."""
    folder = os.environ.get("GH_CONFIG_DIR") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "gh"
    )
    try:
        return os.stat(os.path.join(folder, "hosts.yml")).st_mtime_ns
    except OSError:
        return None


def _fresh() -> bool:
    now, stamp = time.monotonic(), _gh_accounts_stamp()
    return not (
        _command_token is None
        or stamp != _command_token[1]
        or now - _command_token[0] >= (TOKEN_SECONDS if _command_token[2] else SIGNED_OUT_SECONDS)
    )


def _token_lock() -> asyncio.Lock:
    """One lookup at a time on this event loop, so callers that find the cache stale together share it."""
    global _token_lock_state
    loop = asyncio.get_running_loop()
    if _token_lock_state is None or _token_lock_state[0] is not loop:
        _token_lock_state = (loop, asyncio.Lock())
    return _token_lock_state[1]


async def current_token() -> str:
    """The token of gh's sign-in, as the proxy sends it to GitHub; "" when gh is signed out."""
    global _command_token
    if not _fresh():
        async with _token_lock():
            if not _fresh():
                now, stamp = time.monotonic(), _gh_accounts_stamp()
                try:
                    _command_token = (now, stamp, await token())
                except SignInRequired:
                    _command_token = (now, stamp, "")
    return _command_token[2]


async def commands_signed_in() -> bool:
    """Whether git and gh in commands reach GitHub signed in: gh's sign-in, through the proxy."""
    return bool(await current_token())


async def inspect(root: Path | None) -> dict:
    """Whether gh is here, and the GitHub account it is signed in to, as `gh auth status` says."""
    result = {"installed": False, "version": "", "accounts": [], "error": ""}
    try:
        binary = await _gh(root)
    except ValueError as error:
        return {**result, "error": str(error)}
    result["installed"] = True
    try:
        result["version"] = (await run_cli([binary, "--version"], timeout=15)).strip().splitlines()[0]
    except (ValueError, OSError, IndexError):
        return {**result, "error": "The GitHub CLI on this Mac did not start."}
    if (found := re.search(r"(\d+)\.(\d+)", result["version"])) and (int(found[1]), int(found[2])) < OLDEST:
        return {
            **result,
            "error": f"This needs GitHub CLI 2.40 or later; this Mac has {result['version']}. "
            "Update it, then check again.",
        }
    said: list[str] = []
    try:
        # gh prints its status to either stream, so both are read.
        said.append(
            await run_cli(
                [binary, "auth", "status", "--active", "--hostname", HOST],
                timeout=30,
                on_stderr_line=said.append,
            )
        )
    except (ValueError, OSError):
        return result  # Not signed in, which the setup card takes care of.
    if found := SIGNED_IN.search(ANSI.sub("", "\n".join(said))):
        result["accounts"] = [{"id": found[1], "label": found[1]}]
    return result


async def signed_in_account(root: Path | None = None) -> str:
    """The GitHub account gh is signed in to on this Mac, or "" when it is not."""
    if root is None:
        from .config import get_settings

        root = get_settings().data_dir / "integrations"
    accounts = (await inspect(root))["accounts"]
    return accounts[0]["id"] if accounts else ""


class GitHubSignIn:
    """`gh auth login` with GitHub's device flow, one at a time.

    Run without a terminal, gh prints a one-time code instead of waiting for Enter or opening a
    browser. The setup card shows the code and a button that opens GitHub's device page; the
    person enters it there, and gh saves the sign-in as it would in Terminal.
    """

    def __init__(self, root: Path):
        self.root = root
        self.task: asyncio.Task | None = None
        self.state = {"running": False, "code": "", "account": "", "error": ""}

    def start(self) -> dict:
        # A second card, or the same card opened again, joins the sign-in already under way.
        if self.task is None or self.task.done():
            self.state = {"running": True, "code": "", "account": "", "error": ""}
            self.task = asyncio.create_task(self._login())
        return self.status()

    def status(self) -> dict:
        return dict(self.state)

    async def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.state = {"running": False, "code": "", "account": "", "error": ""}

    async def _login(self) -> None:
        def said(line: str) -> None:
            if found := DEVICE_CODE.search(ANSI.sub("", line)):
                self.state["code"] = found[1].upper()

        try:
            # GitHub's device code lasts fifteen minutes; gh waits for it that long.
            await run_cli(
                [await _gh(self.root), "auth", "login", "--hostname", HOST, "--web"],
                timeout=16 * 60,
                on_stdout_line=said,
                on_stderr_line=said,
            )
            accounts = (await inspect(self.root))["accounts"]
            if not accounts:
                raise ValueError("gh finished without a sign-in")
            self.state["account"] = accounts[0]["id"]
        except Exception:  # noqa: BLE001 - gh's output is not repeated; it is not the person's to read
            self.state["error"] = "GitHub sign-in did not finish. Try again."
        finally:
            self.state.update(running=False, code="")
