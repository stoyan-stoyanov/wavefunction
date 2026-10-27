# Third-party components and notices

The root MIT license covers Wavefunction’s original code and documentation. Dependencies and incorporated third-party material retain their respective licenses. A root MIT license does not relicense those components.

## Incorporated source

| Component | Source / version | License | Notice location |
| --- | --- | --- | --- |
| MCP catalog descriptions and endpoint definitions | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/tree/main/optional-mcps), reviewed 2026-09-09 | MIT, Copyright © 2025 Nous Research | `backend/src/wavefunction/vendor/HERMES-LICENSE.txt` |

## Connector and model provider logos

Connector logos are bundled under `frontend/public/connectors`. Most use Simple Icons 16.30.0 (CC0); its license is included alongside the assets. Other marks come from official provider favicon assets. `manifest.json` records provenance. Brand marks identify their services and remain trademarks of their respective owners.

Model provider logos are bundled under `frontend/public/providers`, with source versions recorded in its `README.md`. These use Simple Icons (CC0) and share the license bundled with the connector assets. The Vercel provider reuses the existing connector logo.

## Dependency inventory

The exact dependency versions are recorded in `frontend/package-lock.json`, `backend/uv.lock`, and `src-tauri/Cargo.lock`. These files define the reproducible dependency graph; this document is an overview, not a substitute for all upstream notices.

Major components include React (MIT), Tauri (MIT or Apache-2.0), FastAPI (MIT), LangChain/LangGraph (MIT), and the Inter font (SIL Open Font License). Verify the license and notices of the exact resolved version when preparing a binary distribution.

### Development packaging: PyInstaller

Wavefunction's desktop sidecar uses PyInstaller and includes Python and its libraries inside the app.

PyInstaller is GPL-licensed with an exception allowing distribution of applications built with it under other licenses. Preserve the applicable notices and inspect bundled dependencies independently. See [PyInstaller’s license explanation](https://pyinstaller.org/en/stable/license.html).

## Release requirement

`scripts/release-notices.py` collects installed frontend notices, exact runtime Python metadata/licenses and hash-verified source distributions, and target-resolved Rust notices and hash-verified crate sources under `dist/legal`. The [release workflow](.github/workflows/release.yml) runs it for every release: the notices ship inside the app under `Contents/Resources/legal`, and the notices with the source archives are attached to the GitHub release as `…-notices-and-sources.zip`. Local `npm run desktop:build` builds do not include them. `scripts/LIBRARY_REPLACEMENT.md` documents replacement and rebuild procedures.

The generator includes dependencies conservatively, including some compilation-only packages. These collected records apply to the versions in that release, rather than a blanket statement about future dependency changes.

Any future incorporation of third-party code requires a separate provenance and license review.
