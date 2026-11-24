"""Check release files before anyone installs them, from `npm run release:local` or the Release workflow.

Run with backend/.venv/bin/python (it needs `cryptography`), given the folder that holds the files,
normally dist/release:

- every file matches SHA256SUMS;
- Gatekeeper accepts the DMG and the app inside it as notarized Developer ID, and both staples hold;
- `syspolicy_check distribution` passes and the app carries its third-party notices;
- the app's identifier and version are this checkout's;
- the updater archive holds only the app, with no macOS `._` metadata entries, its signature verifies
  against the public key in tauri.conf.json, and latest.json carries that signature and archive.

With --ad-hoc it checks files from the ad-hoc packaging path instead (docs/RELEASING.md, "Checking
the packaging", which CI's macos-app job runs): the app's signatures hold under `codesign --strict`
in place of notarization, Gatekeeper and syspolicy_check, and --pubkey names the throwaway updater
key's public file to check the signature against.

The DMG is mounted read-only in a temporary folder and detached afterwards.
"""

import argparse
import base64
import hashlib
import json
import plistlib
import subprocess
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


def app_inside(dmg: Path, ad_hoc: bool) -> None:
    mount = Path(tempfile.mkdtemp(prefix="wavefunction-release-"))
    run("/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(dmg))
    try:
        app = mount / f"{CONFIG['productName']}.app"
        if ad_hoc:
            run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
        else:
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


def updater(folder: Path, archive: Path, pubkey: str) -> None:
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
    public = base64.b64decode(base64.b64decode(pubkey).decode().splitlines()[1])
    text = (folder / f"{archive.name}.sig").read_text().strip()
    lines = base64.b64decode(text).decode().splitlines()
    signed = base64.b64decode(lines[1])
    if signed[2:10] != public[2:10]:
        raise SystemExit("The updater archive is signed with a different key than the one expected.")
    data = archive.read_bytes()
    # Minisign's "ED" signs the BLAKE2b-512 hash of the file rather than the file itself.
    message = hashlib.blake2b(data, digest_size=64).digest() if signed[:2] == b"ED" else data
    key = Ed25519PublicKey.from_public_bytes(public[10:])
    key.verify(signed[10:], message)
    comment = lines[2].removeprefix("trusted comment: ").encode()
    key.verify(base64.b64decode(lines[3]), signed[10:] + comment)
    latest = json.loads((folder / "latest.json").read_text())
    # By name: other platforms' entries may join this one in the same file.
    platform = latest["platforms"].get("darwin-aarch64")
    if (
        platform is None
        or latest["version"] != CONFIG["version"]
        or platform["signature"] != text
        or not platform["url"].endswith(archive.name)
    ):
        raise SystemExit("latest.json does not describe this archive and its signature.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?", type=Path, default=ROOT / "dist/release")
    parser.add_argument("--ad-hoc", action="store_true", help="files from the ad-hoc packaging path")
    parser.add_argument("--pubkey", type=Path, help="the updater public key file, if not tauri.conf.json's")
    args = parser.parse_args()
    folder = args.folder.resolve()
    pubkey = args.pubkey.read_text().strip() if args.pubkey else CONFIG["plugins"]["updater"]["pubkey"]
    stem = f"{CONFIG['productName']}-{CONFIG['version']}-macos-arm64"
    dmg, archive = folder / f"{stem}.dmg", folder / f"{stem}.app.tar.gz"
    checksums(folder)
    print("ok  files match SHA256SUMS")
    if args.ad_hoc:
        app_inside(dmg, ad_hoc=True)
        print(
            "ok  app inside signed ad hoc and valid under --strict, carries its notices, right id and version"
        )
    else:
        notarized(dmg, "-t", "open", "--context", "context:primary-signature")
        print("ok  DMG notarized, stapled and accepted by Gatekeeper")
        app_inside(dmg, ad_hoc=False)
        print(
            "ok  app inside notarized and stapled, passes syspolicy_check, carries its notices, right id and version"
        )
    updater(folder, archive, pubkey)
    print("ok  updater archive holds only the app, its signature verifies and latest.json matches")
    print(f"Release files verified: {dmg}")


if __name__ == "__main__":
    main()
