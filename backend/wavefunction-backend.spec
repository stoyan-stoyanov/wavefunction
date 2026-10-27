from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

project_dir = Path(SPEC).parent
# Skill documents are data, not modules, so PyInstaller's import analysis never sees them.
# Without this they are silently missing from the frozen sidecar and the agent loses its
# guidance with no error.
datas = [
    (str(project_dir / "src" / "wavefunction" / "microsoft_graph.ps1"), "wavefunction"),
    (str(project_dir / "src" / "wavefunction" / "skills"), "wavefunction/skills"),
    (str(project_dir / "src" / "wavefunction" / "coding_skills"), "wavefunction/coding_skills"),
    # The desktop image's build context (Dockerfile + action server). Data for the same
    # reason as the skills: nothing imports it, it is copied out and handed to `docker build`.
    (str(project_dir / "src" / "wavefunction" / "sandbox_image"), "wavefunction/sandbox_image"),
]
# Run only as `-m` modules — the bundled connectors and the file tools' helper — so nothing
# imports them where PyInstaller's analysis would see.
hiddenimports = ["wavefunction.microsoft_mcp", "wavefunction.mail_mcp", "wavefunction.n8n_mcp", "wavefunction.fileops", "wavefunction.pty_process"]

for package in (
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langchain_ollama",
    "ollama",
    "langgraph",
    "langgraph_checkpoint",
    "langgraph_sdk",
    "pydantic",
    "pydantic_settings",
    "uvicorn",
    # Reached from scripts the coding agent writes, so nothing imports them at analysis time
    # and PyInstaller would otherwise leave them out entirely.
    "openpyxl",
    "pptx",
    # The document tools import this lazily, inside the tool body, so the same applies.
    "docx",
):
    # Their modules are compiled into the archive; the .py sources beside them would only be a
    # second copy.
    package_datas, package_binaries, package_imports = collect_all(package, include_py_files=False)
    datas += package_datas
    hiddenimports += package_imports

# The agent's `python3` outside a project environment is this interpreter, so it carries the
# standard library a regular one has (`python3 -m unittest`, `unittest.mock`, `json.tool`), not
# just the modules the server imports. Without GUI, demo and test packages; virtual environments
# made from a frozen interpreter would not work, so `venv` is refused with a pointer instead.
# Private modules come with what imports them, except `_pylong`, which CPython loads from C for
# large integer arithmetic.
for name in sorted(sys.stdlib_module_names):
    if name.startswith("_") and name != "_pylong" or name in {"tkinter", "turtle", "turtledemo", "idlelib", "lib2to3", "test",
                                        "this", "antigravity", "ensurepip", "venv"}:
        continue
    hiddenimports += collect_submodules(name, filter=lambda module: ".test" not in module)

for distribution in (
    "langchain",
    "langchain-core",
    "langchain-openai",
    "langchain-ollama",
    "ollama",
    "langgraph",
    "langgraph-checkpoint",
    "langgraph-checkpoint-sqlite",
    "langgraph-prebuilt",
    "pydantic",
):
    datas += copy_metadata(distribution)

analysis = Analysis(
    [str(project_dir / "src" / "wavefunction" / "__main__.py")],
    pathex=[str(project_dir / "src")],
    binaries=[(str(Path(sys.executable).parent / "rg"), ".")],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Development tools the environment has and the app never imports: syntax highlighting,
    # packaging, and the file watcher behind uvicorn's --reload.
    excludes=["pytest", "ruff", "pygments", "setuptools", "_distutils_hack", "pkg_resources", "watchfiles"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    contents_directory="_internal",
    name="wavefunction-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
# Keep the runtime beside the executable inside the app. A onefile build would
# extract these libraries and resources again on every desktop launch.
distribution = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="wavefunction-backend",
)
