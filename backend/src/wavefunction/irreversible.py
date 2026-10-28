"""Commands that can't be undone. They wait for the user in every mode, a chat with full access
included: a fixed check, as Codex's dangerous-command rules and Hermes' pattern list are.

It catches an overeager agent, not a determined one: a script that deletes files passes.
Project file deletions need complete pre-command recovery bytes. Generated dependency/build
folders, task scratch and temporary folders remain disposable by policy."""

import fnmatch
import os
import re
import shlex
import tempfile
from pathlib import Path

DELETES = "Deletes files that undo can't bring back."
DELETES_INPUT = "Deletes files named by another command, which undo may not bring back."
DISCARDS = "Discards uncommitted changes."
CLEANS = "Deletes untracked files."
STASH = "Deletes stashed changes."
BRANCH = "Deletes a branch, with any commits not merged elsewhere."
REMOTE = "Overwrites branches on the remote."
# Inside the sandbox, the GitHub proxy refuses these on its own.
REMOTE_DELETE = "Deletes branches on the remote."
HISTORY = "Rewrites Git history."
DATABASE = "Deletes data in a database."
DOCKER = "Deletes Docker volumes and the data in them."
CLOUD = "Deletes or destroys remote resources."
MAC = "Can erase a disk or break this Mac."
NESTED = "Runs commands nested too deeply to check."

# Temporary folders, resolved because /tmp is a link on macOS.
TEMPORARY = tuple(dict.fromkeys(Path(p).resolve() for p in ("/tmp", tempfile.gettempdir())))
# Separators and redirections; a backtick or $( starts a command too.
OPERATORS = "();<>|&\n`"
REDIRECTIONS = {"<", ">", "<<", ">>", "<<<", "<>", ">&", "<&", ">|", "&>", "&>>"}
# A here-document's `<<` and its closing word, bare, quoted or escaped; not a `<<<` string.
HEREDOC = re.compile(r"""(?<!<)<<(?!<)(-?)[ \t]*(?:'([^'\n]*)'|"([^"\n]*)"|\\?([^\s;&|<>()`'"]+))""")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
# Words that run the command after them, and the options of theirs that take a value.
WRAPPERS = {
    "sudo",
    "doas",
    "env",
    "nohup",
    "time",
    "command",
    "exec",
    "nice",
    "timeout",
    "caffeinate",
    "xargs",
    "npx",
    "bunx",
    "pnpx",
    "pnpm",
    "yarn",
    "npm",
    "bun",
    "uv",
    "uvx",
    "poetry",
    "pipx",
    "run",
    "dlx",
    "if",
    "then",
    "else",
    "elif",
    "do",
    "while",
    "until",
    "!",
    "{",
}
VALUE_OPTIONS = {
    "sudo": {
        "-u",
        "--user",
        "-g",
        "--group",
        "-h",
        "--host",
        "-p",
        "--prompt",
        "-C",
        "--close-from",
        "-D",
        "--chdir",
        "-r",
        "--role",
        "-t",
        "--type",
        "-T",
        "--command-timeout",
        "-U",
        "--other-user",
    },
    "doas": {"-u", "-C"},
    "env": {"-u", "--unset", "-C", "--chdir"},
    "nice": {"-n", "--adjustment"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {
        "-n",
        "--max-args",
        "-I",
        "-L",
        "--max-lines",
        "-P",
        "--max-procs",
        "-s",
        "--max-chars",
        "-E",
        "-d",
        "--delimiter",
        "-a",
        "--arg-file",
    },
    "pnpm": {
        "-C",
        "--dir",
        "--filter",
        "-F",
        "--config",
        "--store-dir",
        "--workspace-concurrency",
        "--loglevel",
        "--changed-files-ignore-pattern",
        "--filter-prod",
        "--test-pattern",
        "--resume-from",
        "--reporter",
        "--prefix",
        "--workspace-packages",
        "--pm-on-fail",
    },
    "npx": {"-p", "--package", "--cache", "--prefix", "--registry", "--userconfig", "--script-shell"},
    "npm": {
        "--package",
        "--prefix",
        "--workspace",
        "-w",
        "--cache",
        "--registry",
        "--userconfig",
        "--script-shell",
    },
    "yarn": {"--cwd", "--use-yarnrc", "--cache-folder", "--modules-folder"},
    "bun": {"--cwd", "--filter", "-F", "--config", "-c", "--env-file", "--preload", "-r"},
    "bunx": {"--package", "-p"},
    "uv": {
        "--directory",
        "--project",
        "--package",
        "--python",
        "-p",
        "--with",
        "--with-editable",
        "--with-requirements",
        "--index",
        "--default-index",
        "--index-url",
        "--extra-index-url",
        "--config-file",
        "--cache-dir",
        "--env-file",
        "--group",
        "--no-group",
        "--only-group",
        "--extra",
        "--no-extra",
        "--color",
        "--allow-insecure-host",
        "--config-setting",
        "--config-settings-package",
        "--exclude-newer",
        "--exclude-newer-package",
        "--find-links",
        "--fork-strategy",
        "--index-strategy",
        "--keyring-provider",
        "--link-mode",
        "--no-binary-package",
        "--no-build-isolation-package",
        "--no-build-package",
        "--no-sources-package",
        "--prerelease",
        "--python-platform",
        "--refresh-package",
        "--reinstall-package",
        "--resolution",
        "--upgrade-group",
        "--upgrade-package",
        "-C",
        "-P",
        "-f",
        "-i",
        "-w",
    },
    "uvx": {
        "--from",
        "--with",
        "--with-editable",
        "--with-requirements",
        "--python",
        "-p",
        "--directory",
        "--project",
        "--index",
        "--default-index",
        "--index-url",
        "--extra-index-url",
        "--config-file",
        "--cache-dir",
        "--env-file",
        "--color",
        "--allow-insecure-host",
        "--config-setting",
        "--config-settings-package",
        "--exclude-newer",
        "--exclude-newer-package",
        "--find-links",
        "--fork-strategy",
        "--index-strategy",
        "--keyring-provider",
        "--link-mode",
        "--no-binary-package",
        "--no-build-isolation-package",
        "--no-build-package",
        "--no-sources-package",
        "--prerelease",
        "--python-platform",
        "--refresh-package",
        "--reinstall-package",
        "--resolution",
        "--upgrade-group",
        "--upgrade-package",
        "--build-constraints",
        "--constraints",
        "--overrides",
        "--torch-backend",
        "-C",
        "-P",
        "-f",
        "-i",
        "-w",
        "-b",
        "-c",
    },
    "poetry": {"--directory", "-C", "--project", "-P"},
    "pipx": {"--spec", "--python", "--index-url", "--pip-args"},
}
VALUE_OPTIONS["pnpx"] = VALUE_OPTIONS["pnpm"] | {"--package", "-p"}
# Package managers' own commands that uninstall a dependency (`npm rm lodash`), not file deletes.
PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
UNINSTALLS = {"rm", "remove", "uninstall", "un", "unlink", "r"}
# This deliberate cleanup exemption does not claim these folders are backed up.
GENERATED = {"node_modules", ".venv", "venv", "__pycache__", "target", "dist", "build", ".next"}
DELETERS = {"rm", "unlink", "shred", "srm"}
DB_CLIENTS = {
    "psql",
    "mysql",
    "mariadb",
    "sqlite3",
    "pgcli",
    "mycli",
    "duckdb",
    "clickhouse-client",
    "sqlcmd",
    "cockroach",
    "mongosh",
    "mongo",
    "supabase",
    "prisma",
    "turso",
}
SQL = re.compile(
    r"\b(drop\s+(database|schema|table|owned)|truncate)\b|\bdelete\s+from\s+[\w.\"]+\s*(;|$|[\"']|returning\b)"
    r"|\bdropDatabase\s*\(",
    re.IGNORECASE,
)
SQL_START = {"drop", "truncate", "delete"}
DEVICE = re.compile(r"/dev/(r?disk|sd|nvme|hd)")
SYSTEM = {
    Path(p)
    for p in (
        "/",
        "/System",
        "/Library",
        "/Applications",
        "/Users",
        "/usr",
        "/bin",
        "/sbin",
        "/etc",
        "/var",
        "/private",
        "/opt",
    )
}


def command_danger(
    command: str,
    *,
    cwd: Path,
    project: Path | None,
    scratch: Path,
    outside: bool,
    captured: dict | None = None,
) -> str | None:
    """Classify using the existing pre-command capture, never the mere presence of .git.

    None marks a disposable root; a set contains the exact files with recovery bytes. Unknown
    directory/glob contents ask, rather than treating ignored or omitted files as backed up.
    """
    undoable: dict[Path, set[Path] | None] = {scratch: None}
    if project is not None and (project / ".git").exists():
        root = project.resolve()
        undoable[root] = {
            root / name for name, row in (captured or {}).items() if row.get("data") is not None
        }
    return irreversible(command, cwd=cwd, undoable=undoable, proxy_protects_remote_delete=not outside)


def irreversible(
    command: str,
    *,
    cwd: Path,
    undoable: dict[Path, set[Path] | None],
    depth: int = 0,
    proxy_protects_remote_delete: bool = False,
) -> str | None:
    """Why `command` can't be undone, or None. `undoable` holds the folders where deleting is
    recoverable or harmless; relative paths resolve against `cwd`."""
    if depth > 3:
        return NESTED
    text, bodies = _heredocs(command)
    try:
        lexed = _lex(text)
    except ValueError:
        # Unbalanced quotes: what can still be seen of a forced or recursive delete asks.
        return DELETES if re.search(r"(^|[\s;&|(`])(sudo\s+)?rm\s+-\w*[rRf]", command) else None
    tokens = [token for token, _ in lexed]
    roots = {root.resolve(): files for root, files in undoable.items()} | dict.fromkeys(TEMPORARY)
    words, read, commands = [], [], []
    redirection_target = False
    for index, (token, operator) in enumerate(lexed):
        if operator:
            redirection_target = False
            if ">" in token and index + 1 < len(tokens) and DEVICE.match(tokens[index + 1]):
                return MAC
            if token in REDIRECTIONS:
                if token == "<<" and bodies and (body := bodies.pop(0)) is not None:
                    read.append(body)
                redirection_target = True
                continue
            commands.append((words, token, "\n".join(read)))
            words, read = [], []
        elif redirection_target:
            redirection_target = False
        else:
            words.append(token)
    commands.append((words, "", "\n".join(read)))
    # Track cd within its successful && chain. A later sequential list may run even
    # if that cd failed; subshells, pipelines and background jobs need their own scope.
    sequential = {";", "\n"}
    shared_shell = all(
        token == "&&" or token in sequential or token in REDIRECTIONS for token, operator in lexed if operator
    )
    database, here, queries = False, cwd, []
    previous, cwd_changed, carried = "", False, ""
    for words, separator, body in commands:
        # A pipeline passes what its first command reads on: `cat <<EOF | psql`.
        body = "\n".join(filter(None, (carried, body)))
        carried = body if separator in {"|", "|&"} else ""
        if previous in sequential and cwd_changed:
            here = None
        previous = separator
        program, args, piped = _unwrap(words)
        if not program:
            continue
        name = Path(program).name
        if name == "cd":
            cwd_changed = True
            target = os.path.expanduser(args[0] if args else "~")
            # Somewhere only the shell knows: relative paths after it count as outside.
            here = (
                None
                if len(args) > 1
                or not shared_shell
                or separator != "&&"
                or here is None
                or target.startswith(("$", "-"))
                else Path(here, target)
            )
            continue
        # A here-document is input to its command: commands to a shell, queries to a database client.
        readers = {Path(word).name for word in words} if body else set()
        if readers & DB_CLIENTS:
            database, queries = True, [*queries, body]
        database = database or name in DB_CLIENTS or name.lower() in SQL_START
        if name in SHELLS or name == "eval":
            found, scripts = None, [" ".join(args) if name == "eval" else _shell_payload(args)]
        else:
            found, scripts = _check(name, args, piped, here, roots), []
        for script in filter(None, [*scripts, body if readers & SHELLS else ""]):
            found = found or irreversible(
                script,
                cwd=here or Path("/"),
                undoable=undoable if here else {},
                depth=depth + 1,
                proxy_protects_remote_delete=proxy_protects_remote_delete,
            )
        if found == REMOTE_DELETE and proxy_protects_remote_delete:
            continue
        if found:
            return found
    return DATABASE if database and any(SQL.search(part) for part in (text, *tokens, *queries)) else None


def _heredocs(command: str) -> tuple[str, list[str | None]]:
    """The command without its here-documents' lines, and the body of each unquoted `<<` in
    order. A body is input to the command with the `<<`, not commands of its own. One whose
    closing line never comes is None, and its lines stay commands."""
    lines, kept, bodies, quote, index = command.split("\n"), [], [], "", 0
    while index < len(lines):
        line, index, opened, at = lines[index], index + 1, [], 0
        kept.append(line)
        while at < len(line):
            char = line[at]
            if quote:
                quote = "" if char == quote else quote
                at += 2 if char == "\\" and quote == '"' else 1
            elif char == "<" and (match := HEREDOC.match(line, at)):
                opened.append((match.group(1), next(g for g in match.groups()[1:] if g is not None)))
                at = match.end()
            else:
                quote = char if char in "'\"" else ""
                at += 2 if char == "\\" else 1
        for strip, delimiter in opened:
            end = next(
                (
                    i
                    for i in range(index, len(lines))
                    if (lines[i].lstrip("\t") if strip else lines[i]) == delimiter
                ),
                None,
            )
            bodies.append(None if end is None else "\n".join(lines[index:end]))
            index = index if end is None else end + 1
    return "\n".join(kept), bodies


def _lex(command: str) -> list[tuple[str, bool]]:
    # shlex strips quotes/escapes before returning tokens. Preserve literal punctuation
    # so find's \( ... \) and -exec ... \; remain arguments, not shell boundaries.
    start = 0xE000
    while any(chr(start + i) in command for i in range(len(OPERATORS))):
        start += len(OPERATORS)
    masked = {char: chr(start + i) for i, char in enumerate(OPERATORS)}
    restore = str.maketrans({value: key for key, value in masked.items()})
    text, quote, escaped, word_start = [], "", False, 0
    for index, char in enumerate(command):
        if escaped:
            text.append(masked.get(char, char))
            escaped = False
        elif char == "\\" and quote != "'":
            text.append(char)
            escaped = True
        elif char in "\"'" and (not quote or char == quote):
            quote = "" if quote else char
            text.append(char)
        else:
            if not quote:
                # Only an unquoted number adjacent to a redirection is an fd.
                # Keep spaced or quoted numbers as real command operands.
                if char in "<>":
                    descriptor = command[word_start:index]
                    if descriptor.isascii() and descriptor.isdecimal():
                        del text[-len(descriptor) :]
                if char in " \t\r" + OPERATORS:
                    word_start = index + 1
            text.append(masked.get(char, char) if quote else char)
    lexer = shlex.shlex("".join(text), posix=True, punctuation_chars=OPERATORS)
    lexer.whitespace, lexer.whitespace_split = " \t\r", True
    return [(token.translate(restore), set(token) <= set(OPERATORS)) for token in lexer]


def _unwrap(words: list[str]) -> tuple[str, list[str], bool]:
    """The program a command runs past variable assignments and wrappers such as sudo, its
    arguments, and whether xargs feeds it."""
    index, piped, wrapper, manager = 0, False, "", ""
    while index < len(words):
        word = words[index]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
            index += 1
            continue
        name = Path(word).name
        if manager and name in UNINSTALLS:
            return manager, words[index:], piped
        if name not in WRAPPERS:
            return word, words[index + 1 :], piped
        piped = piped or name == "xargs"
        manager = name if name in PACKAGE_MANAGERS else ""
        # Options after `uv run`, `npm exec`, etc. still belong to that launcher.
        if name not in {"run", "exec", "dlx"}:
            wrapper = name
        index += 1
        while index < len(words) and words[index].startswith("-"):
            option = words[index]
            # pnpm versions accept either bare color flags or these explicit modes.
            color_value = (
                wrapper in {"pnpm", "pnpx"}
                and option == "--color"
                and index + 1 < len(words)
                and words[index + 1] in {"always", "auto", "never"}
            )
            index += 2 if option in VALUE_OPTIONS.get(wrapper, ()) or color_value else 1
            if option == "--":
                break
        if name == "timeout" and index < len(words):
            index += 1
    return "", [], piped


def _shell_payload(args: list[str]) -> str:
    for index, arg in enumerate(args):
        if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg):
            return args[index + 1] if index + 1 < len(args) else ""
    return ""


def _split(args: list[str]) -> tuple[list[str], list[str]]:
    """Options and operands, up to and after a `--`."""
    options, operands, ended = [], [], False
    for arg in args:
        if not ended and arg == "--":
            ended = True
        elif not ended and arg.startswith("-") and arg != "-":
            options.append(arg)
        else:
            operands.append(arg)
    return options, operands


def _short(options: list[str], letters: str, *long: str) -> bool:
    return any(
        option in long or (not option.startswith("--") and any(c in option[1:] for c in letters))
        for option in options
    )


def _within(target: str, cwd: Path | None, roots: dict[Path, set[Path] | None], *, whole: bool) -> bool:
    """Whether deleting `target` stays inside a recoverable folder. `whole` allows the folder
    itself, as for a filtered `find`; a bare wildcard means every file in its folder."""
    target = os.path.expanduser(target)
    # Brace/variable expansion before globs can hide link traversal or .git.
    # Their unexpanded path cannot establish a disposable or captured target.
    if any(char in target for char in "{}$") or (cwd is None and not Path(target).is_absolute()):
        return False
    path = Path(target) if cwd is None or Path(target).is_absolute() else cwd / target
    has_pattern = any(c in target for c in "*?[")
    if has_pattern and (".." in path.parts or target.endswith(("/", "/."))):
        return False
    if not has_pattern:
        # Resolve parent traversal before normalizing away '..'. A trailing slash
        # (or /.) also follows the final link, unlike unlinking the link itself.
        path = (
            path.resolve()
            if target.endswith(("/", "/.")) or path.name == ".."
            else path.parent.resolve() / path.name
        )
    parts = Path(os.path.normpath(path)).parts
    if any(part.startswith(".") and fnmatch.fnmatchcase(".git", part.casefold()) for part in parts):
        return False
    wildcard = False
    for index, part in enumerate(parts):
        if any(c in part for c in "*?["):
            # A wildcard in a parent directory can expand through an unknown link.
            if index < len(parts) - 1:
                return False
            wildcard = True
            parent, everything = Path(*parts[:index]).resolve(), not part.strip("*?.[]")
            path = parent if everything else parent / "file"
            whole = whole and not everything
            break
    else:
        path = Path(*parts)
    path = path.parent.resolve() / path.name if path.name else path.resolve()
    for root, files in roots.items():
        if path.is_relative_to(root) and (whole or path != root):
            if files is None or (not wildcard and path in files):
                return True
            directory = root
            for part in path.relative_to(root).parts:
                directory /= part
                if part in GENERATED and not directory.is_symlink() and directory.is_dir():
                    return True
    return False


def _has(words: list[str], *sequence: str) -> bool:
    return any(words[i : i + len(sequence)] == list(sequence) for i in range(len(words)))


def _check(
    name: str, args: list[str], piped: bool, cwd: Path | None, roots: dict[Path, set[Path] | None]
) -> str | None:
    lowered = [arg.lower() for arg in args]
    words = [arg for arg in lowered if not arg.startswith("-")]
    options, operands = _split(args)
    if name in DELETERS:
        if piped:
            return DELETES_INPUT
        if any(Path(target).name.startswith(".env") for target in operands):
            return DELETES
        return DELETES if any(not _within(t, cwd, roots, whole=False) for t in operands) else None
    if name == "find" and (
        "-delete" in args
        or any(
            arg in ("-exec", "-execdir", "-ok", "-okdir")
            and index + 1 < len(args)
            and Path(_unwrap(args[index + 1 :])[0]).name in DELETERS
            for index, arg in enumerate(args)
        )
    ):
        if {"-L", "-H", "-follow"} & set(args):
            # Following a link can leave even a disposable search root.
            return DELETES
        rest = args[
            next(
                (i for i, a in enumerate(args) if a not in ("-H", "-L", "-P", "-E", "-X", "-d", "-s", "-x")),
                len(args),
            ) :
        ]
        starts = rest[: next((i for i, a in enumerate(rest) if a.startswith(("-", "(", "!"))), len(rest))]
        filtered = any(
            arg.startswith("-") and arg not in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-depth")
            for arg in args
        )
        if any(not _within(s, cwd, roots, whole=filtered) for s in starts or ["."]):
            return DELETES
        # find also passes literal operands to rm; safe search roots don't make those
        # safe. With execdir, their relative paths depend on each matched directory.
        for index, arg in enumerate(args):
            if arg not in ("-exec", "-execdir", "-ok", "-okdir") or index + 1 >= len(args):
                continue
            tail = args[index + 1 :]
            end = next((i for i, a in enumerate(tail) if a in (";", "+")), len(tail))
            program, nested_args, nested_piped = _unwrap(tail[:end])
            if Path(program).name not in DELETERS:
                continue
            fixed = [a for a in nested_args if a != "{}"]
            if any("{}" in a for a in fixed) or _check(
                Path(program).name, fixed, nested_piped, None if arg.endswith("dir") else cwd, roots
            ):
                return DELETES
        return None
    if name == "git":
        return _git(args)
    if name in ("docker", "podman", "docker-compose"):
        if (
            _has(words, "volume", "rm")
            or _has(words, "volume", "remove")
            or _has(words, "volume", "prune")
            or (_has(words, "system", "prune") and "--volumes" in lowered)
            or (
                "down" in words
                or words[:1] == ["rm"]
                or words[:2] in (["container", "rm"], ["container", "remove"])
            )
            and _short(options, "v", "--volumes")
        ):
            return DOCKER
        return None
    if name == "supabase":
        if _has(words, "db", "reset"):
            return DATABASE
        return DOCKER if "stop" in words and "--no-backup" in lowered else None
    if name == "prisma" and (_has(words, "migrate", "reset") or "--force-reset" in lowered):
        return DATABASE
    if name in ("rails", "rake") and any(arg in ("db:drop", "db:reset") for arg in lowered):
        return DATABASE
    if name == "dropdb" or (name == "redis-cli" and {"flushall", "flushdb"} & set(lowered)):
        return DATABASE
    if (
        name in ("terraform", "pulumi")
        and ("destroy" in words or "apply" in words and "-destroy" in lowered)
        or name == "kubectl"
        and "delete" in words
        and not _kubectl_dry_run(args)
        or name == "aws"
        and any(arg in ("rm", "rb") or arg.startswith(("delete-", "terminate-")) for arg in lowered)
        or name == "gcloud"
        and "delete" in lowered
        or name == "gh"
        and (_gh_subcommand_delete(args) or _gh_delete(args))
    ):
        return CLOUD
    if (
        name.startswith(("mkfs", "newfs"))
        or name == "dd"
        and any(arg.startswith("of=") and DEVICE.match(arg[3:]) for arg in args)
        or name == "diskutil"
        and any(
            arg.startswith(("erase", "partition", "zero", "random", "secureerase", "reformat", "delete"))
            for arg in lowered
        )
    ):
        return MAC
    if name in ("chmod", "chown", "chgrp") and _short(options, "R", "--recursive"):
        home = Path.home().resolve()
        for target in operands:
            path = Path(os.path.expanduser(target.replace("$HOME", "~")))
            if path.is_absolute() and (path.resolve() in SYSTEM or path.resolve() == home):
                return MAC
    return None


def _kubectl_dry_run(args: list[str]) -> bool:
    # pflag's NoOptDefVal makes a bare --dry-run a client preview; it does not consume
    # the next word. Repeated flags use the last value. Legacy false/0 mean real writes.
    value = "none"
    for arg in args:
        if arg == "--":
            break
        if arg == "--dry-run":
            value = "client"
        elif arg.startswith("--dry-run="):
            value = arg.partition("=")[2]
    return value in {"client", "server", "unchanged", "true", "True", "TRUE", "t", "T", "1"}


def _gh_subcommand_delete(args: list[str]) -> bool:
    """`gh <resource> delete` and its nested forms (`gh repo deploy-key delete`,
    `gh project item-delete`), wherever gh's repository and host flags put the words."""
    words, index = [], 0
    while index < len(args) and len(words) < 3:
        arg = args[index]
        if arg in ("-R", "--repo", "--hostname"):
            index += 1  # Its value is not a command word.
        elif not arg.startswith("-"):
            words.append(arg.lower())
        index += 1
    # Only gh's own nested commands: elsewhere a third word is an argument (`gh repo view delete`).
    # `gh search` takes free text, and `gh api` has its own method check below.
    return words[:1] not in (["api"], ["search"]) and (
        words[1:2] in (["delete"], ["delete-asset"])
        or any(word.endswith("-delete") for word in words[1:2])
        or words[:2] in (["repo", "deploy-key"], ["repo", "autolink"])
        and words[2:3] == ["delete"]
    )


def _gh_delete(args: list[str]) -> bool:
    # Gate an explicit DELETE request; the proxy still has no API blocklist. Skip the
    # other value-taking flags so a jq expression/header cannot pretend to be a method.
    method, api, index = "", False, 0
    values = {
        "-R",
        "--repo",
        "--hostname",
        "-H",
        "--header",
        "-f",
        "--raw-field",
        "-F",
        "--field",
        "--input",
        "-q",
        "--jq",
        "-t",
        "--template",
        "--cache",
    }
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break
        if arg == "--method":
            index += 1
            method = args[index] if index < len(args) else ""
        elif arg.startswith("--method="):
            method = arg.partition("=")[2]
        elif arg in values:
            index += 1
        elif arg.startswith("-") and not arg.startswith("--"):
            # pflag permits -iX DELETE and -iXDELETE. A value-taking shorthand
            # consumes the rest of its group (or the following word).
            for offset, flag in enumerate(arg[1:], start=1):
                if flag not in "XRHfFqt":
                    continue
                value = arg[offset + 1 :].removeprefix("=")
                if not value:
                    index += 1
                    value = args[index] if index < len(args) else ""
                if flag == "X":
                    method = value
                break
        elif arg == "api":
            api = True
        index += 1
    return api and method.upper() == "DELETE"


def _git(args: list[str]) -> str | None:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += (
            2
            if args[index]
            in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--exec-path")
            else 1
        )
    if index >= len(args):
        return None
    sub, rest = args[index], args[index + 1 :]
    options, operands = _split(rest)
    if sub == "worktree" and operands[:1] == ["remove"]:
        # Even without --force Git removes ignored local data in a clean worktree.
        return DELETES
    if sub == "reset" and "--hard" in options:
        return DISCARDS
    if (
        sub == "checkout"
        and ("--" in rest or "." in operands)
        or sub in ("checkout", "switch")
        and _short(options, "f", "--force", "--discard-changes")
    ):
        return DISCARDS
    if sub == "restore" and not (
        {"--staged", "-S"} & set(options) and not {"--worktree", "-W"} & set(options)
    ):
        return DISCARDS
    if sub == "clean" and _short(options, "f", "--force"):
        return CLEANS
    if sub == "stash" and operands[:1] in (["drop"], ["clear"]):
        return STASH
    if sub == "branch" and (
        any(re.fullmatch(r"-[a-zA-Z]*D[a-zA-Z]*", o) for o in options)
        or _short(options, "d", "--delete")
        and _short(options, "f", "--force")
    ):
        return BRANCH
    # `--mirror` force-updates every remote branch as well as deleting the ones gone locally.
    if sub == "push" and (
        _short(options, "f")
        or any(o.startswith("--force") for o in options)
        or "--mirror" in options
        or any(ref.startswith("+") for ref in operands[1:])
    ):
        return REMOTE
    if sub == "push" and (
        _short(options, "d", "--delete", "--mirror", "--prune")
        or any(ref.startswith(":") for ref in operands[1:])
    ):
        return REMOTE_DELETE
    if (
        sub in ("filter-branch", "filter-repo")
        or sub == "reflog"
        and operands[:1] in (["expire"], ["delete"])
    ):
        return HISTORY
    if sub == "update-ref" and "-d" in options:
        return HISTORY
    return None
