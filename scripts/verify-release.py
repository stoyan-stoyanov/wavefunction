"""Check release files before anyone installs them, from `npm run release:local` or the Release workflow.

Run with backend/.venv/bin/python (it needs `cryptography`), given the folder that holds the files,
normally dist/release:

- every file matches SHA256SUMS;
- Gatekeeper accepts the DMG and the app inside it as notarized Developer ID, and both staples hold;
- `syspolicy_check distribution` passes and the app carries its third-party notices;
- the app's identifier and version are this checkout's;
- the updater archive holds only the app, with no macOS `._` metadata entries, its signature verifies
  against the public key in tauri.conf.json, and latest.json carries that signature and archive.

The DMG is mounted read-only in a temporary folder and detached afterwards.
"""

import base64
import hashlib
import json
import plistlib
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())


def run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def notarized(path: Path, *extra: str) -> None:
    assessment = run("/usr/sbin/spctl", "-a", "-vv", *extra, str(path))
    if "source=Notarized Developer ID" not in assessment:
        raise SystemExit(f"Gatekeeper does not see {path.name} as notarized Developer ID:\n{assessment}")
    run("/usr/bin/xcrun", "stapler", "validate", str(path))


def checksums(folder: Path) -> None:
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"{name} does not match SHA256SUMS")


def app_inside(dmg: Path) -> None:
    mount = Path(tempfile.mkdtemp(prefix="wavefunction-release-"))
    run("/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(dmg))
    try:
        app = mount / f"{CONFIG['productName']}.app"
        notarized(app)
        run("/usr/bin/syspolicy_check", "distribution", str(app))
        if not (app / "Contents/Resources/legal/THIRD_PARTY_NOTICES.md").is_file():
            raise SystemExit("The app carries no third-party notices.")
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        found = (info["CFBundleIdentifier"], info["CFBundleShortVersionString"])
        if found != (CONFIG["identifier"], CONFIG["version"]):
            raise SystemExit(f"The app is {found}, not {(CONFIG['identifier'], CONFIG['version'])}.")
    finally:
        run("/usr/bin/hdiutil", "detach", str(mount))
        mount.rmdir()


def updater(folder: Path, archive: Path) -> None:
    # The updater drops each entry's first path component, so anything outside the app, such as
    # the `._Wavefunction.app` that macOS tar adds, lands on its extraction folder and fails.
    app = f"{CONFIG['productName']}.app"
    with tarfile.open(archive) as members:
        stray = [
            name
            for name in members.getnames()
            if name.split("/", 1)[0] != app or name.rsplit("/", 1)[-1].startswith("._")
        ]
    if stray:
        raise SystemExit(
            f"The updater archive holds {len(stray)} entries the updater cannot install, "
            f"starting with {stray[0]!r}. Build it without Mac metadata."
        )
    public = base64.b64decode(
        base64.b64decode(CONFIG["plugins"]["updater"]["pubkey"]).decode().splitlines()[1]
    )
    text = (folder / f"{archive.name}.sig").read_text().strip()
    lines = base64.b64decode(text).decode().splitlines()
    signed = base64.b64decode(lines[1])
    if signed[2:10] != public[2:10]:
        raise SystemExit("The updater archive is signed with a different key than tauri.conf.json names.")
    data = archive.read_bytes()
    # Minisign's "ED" signs the BLAKE2b-512 hash of the file rather than the file itself.
    message = hashlib.blake2b(data, digest_size=64).digest() if signed[:2] == b"ED" else data
    key = Ed25519PublicKey.from_public_bytes(public[10:])
    key.verify(signed[10:], message)
    comment = lines[2].removeprefix("trusted comment: ").encode()
    key.verify(base64.b64decode(lines[3]), signed[10:] + comment)
    latest = json.loads((folder / "latest.json").read_text())
    platform = next(iter(latest["platforms"].values()))
    if (
        latest["version"] != CONFIG["version"]
        or platform["signature"] != text
        or not platform["url"].endswith(archive.name)
    ):
        raise SystemExit("latest.json does not describe this archive and its signature.")


def main() -> None:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist/release").resolve()
    stem = f"{CONFIG['productName']}-{CONFIG['version']}-macos-arm64"
    dmg, archive = folder / f"{stem}.dmg", folder / f"{stem}.app.tar.gz"
    checksums(folder)
    print("ok  files match SHA256SUMS")
    notarized(dmg, "-t", "open", "--context", "context:primary-signature")
    print("ok  DMG notarized, stapled and accepted by Gatekeeper")
    app_inside(dmg)
    print(
        "ok  app inside notarized and stapled, passes syspolicy_check, carries its notices, right id and version"
    )
    updater(folder, archive)
    print("ok  updater archive holds only the app, its signature verifies and latest.json matches")
    print(f"Release files verified: {dmg}")


if __name__ == "__main__":
    main()
