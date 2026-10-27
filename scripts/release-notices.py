"""Collect exact installed dependency notices and hash-verified source archives.

Run with backend/.venv/bin/python after npm ci, uv sync and cargo fetch.
Generated files stay in dist/legal for inclusion in a published distribution.
"""

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist/legal"
NOTICES = OUT / "notices"
SOURCES = OUT / "sources"
for directory in (NOTICES, SOURCES):
    directory.mkdir(parents=True, exist_ok=True)
records = []
expressions = set()


def fetch(url, destination, expected=None):
    if not destination.exists():
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        if expected and hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError(f"Source checksum mismatch: {url}")
        destination.write_bytes(data)
    if expected and hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f"Cached source checksum mismatch: {destination.name}")


def add(group, name, version, license_expression, files, source):
    target = NOTICES / group / f"{name.replace('/', '_')}@{version}"
    target.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(files):
        if path.is_file():
            shutil.copyfile(path, target / f"{index}-{path.name}")
    if license_expression:
        expressions.add(license_expression)
    records.append(
        {
            "group": group,
            "name": name,
            "version": version,
            "license": license_expression,
            "source": source,
            "notices": str(target.relative_to(NOTICES)),
        }
    )


def notice_files(root):
    return [
        p
        for p in root.rglob("*")
        if p.is_file() and any(key in p.name.lower() for key in ("license", "copying", "copyright", "notice"))
    ]


# Include all installed production npm packages, conservatively including build-time packages.
lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
for relative, spec in lock["packages"].items():
    directory = ROOT / "frontend" / relative
    if not relative or spec.get("dev") or not directory.exists():
        continue
    package = json.loads((directory / "package.json").read_text())
    if package["version"] != spec["version"]:
        raise RuntimeError(f"Installed npm version differs from lock: {relative}")
    files = [
        p
        for p in directory.iterdir()
        if p.is_file()
        and any(key in p.name.lower() for key in ("license", "copying", "copyright", "notice", "readme"))
    ]
    add("npm", package["name"], package["version"], spec.get("license", ""), files, spec.get("resolved"))

# Runtime Python dependency selection uses exported environment markers.
uv = tomllib.loads((ROOT / "backend/uv.lock").read_text())
packages = {p["name"]: p for p in uv["package"]}
downloads = []
requirements = subprocess.check_output(
    [
        "uv",
        "export",
        "--project",
        str(ROOT / "backend"),
        "--locked",
        "--no-dev",
        "--no-emit-project",
        "--no-hashes",
    ],
    text=True,
)
for line in requirements.splitlines():
    if not re.match(r"^[a-zA-Z0-9_.-]+==", line):
        continue
    req = Requirement(line.rstrip(" \\"))
    if req.marker and not req.marker.evaluate():
        continue
    dist = metadata.distribution(req.name)
    if dist.version not in req.specifier:
        raise RuntimeError(f"Installed version differs from lock: {req.name}")
    files = [
        Path(dist.locate_file(f))
        for f in dist.files or []
        if any(key in Path(f).name.lower() for key in ("license", "copying", "copyright", "notice"))
    ]
    spec = packages[req.name]
    source = spec.get("sdist")
    expression = dist.metadata.get("License-Expression") or dist.metadata.get("License", "")
    # Preserve full metadata where legacy license fields contain prose or classifier information.
    target = NOTICES / "python" / f"{req.name}@{dist.version}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "METADATA.txt").write_text(dist.read_text("METADATA") or "")
    add("python", req.name, dist.version, expression, files, source["url"] if source else None)
    if source:
        path = SOURCES / source["url"].rsplit("/", 1)[1]
        downloads.append((source["url"], path, source["hash"].split(":")[1]))

# Include the resolved target's crate sources, with lock-verified checksums.
cargo = json.loads(
    subprocess.check_output(
        [
            "cargo",
            "metadata",
            "--locked",
            "--offline",
            "--format-version",
            "1",
            "--manifest-path",
            str(ROOT / "src-tauri/Cargo.toml"),
            "--filter-platform",
            "aarch64-apple-darwin",
        ]
    )
)
resolved = {node["id"] for node in cargo["resolve"]["nodes"]}
crate_lock = tomllib.loads((ROOT / "src-tauri/Cargo.lock").read_text())
checksums = {(p["name"], p["version"]): p.get("checksum") for p in crate_lock["package"]}
for package in cargo["packages"]:
    if package["id"] not in resolved or not package["source"]:
        continue
    directory = Path(package["manifest_path"]).parent
    name, version = package["name"], package["version"]
    archive = directory.parents[1].parent / "cache" / directory.parent.name / f"{name}-{version}.crate"
    expected = checksums[(name, version)]
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f"Missing or invalid crate archive: {archive}")
    shutil.copyfile(archive, SOURCES / archive.name)
    add(
        "rust",
        name,
        version,
        package["license"],
        notice_files(directory),
        f"https://crates.io/api/v1/crates/{name}/{version}/download",
    )

with ThreadPoolExecutor(max_workers=6) as pool:
    list(pool.map(lambda args: fetch(*args), downloads))
# Some wheels omit license files. Preserve upstream sdist notices without extracting executable content.
for _, archive, _ in downloads:
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            if member.isfile() and any(
                key in Path(member.name).name.lower() for key in ("license", "copying", "copyright", "notice")
            ):
                dest = NOTICES / "source-notices" / archive.name
                dest.mkdir(parents=True, exist_ok=True)
                (dest / member.name.replace("/", "_")).write_bytes(source.extractfile(member).read())

# Standard texts supplement upstream copyright notices and source headers.
standard = NOTICES / "license-texts"
standard.mkdir(exist_ok=True)
ids = set()
for expression in expressions:
    if expression and len(expression) < 110 and "\n" not in expression:
        ids.update(re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", expression))
ids |= {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "GPL-3.0-only", "MPL-2.0", "OFL-1.1"}
ignored = {"OR", "AND", "WITH", "License", "Apache", "Version", "Modified", "BSD", "PSFL"}
for license_id in sorted(ids - ignored):
    fetch(
        f"https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/{license_id}.txt",
        standard / f"{license_id}.txt",
    )
for path in [
    ROOT / "LICENSE",
    ROOT / "THIRD_PARTY_NOTICES.md",
    ROOT / "backend/src/wavefunction/vendor/HERMES-LICENSE.txt",
]:
    shutil.copyfile(path, NOTICES / path.name)
shutil.copyfile(ROOT / "scripts/LIBRARY_REPLACEMENT.md", NOTICES / "LIBRARY_REPLACEMENT.md")
(NOTICES / "inventory.json").write_text(json.dumps(records, indent=2) + "\n")
(SOURCES / "SHA256SUMS").write_text(
    "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
        for p in sorted(SOURCES.iterdir())
        if p.is_file() and p.name != "SHA256SUMS"
    )
)
print(
    f"Collected {len(records)} dependency records and {len(list(SOURCES.iterdir()))} source archives in {OUT}"
)
