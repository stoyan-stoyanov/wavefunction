from __future__ import annotations

import asyncio
import sys

import pytest
from fastapi.testclient import TestClient

from wavefunction import main
from wavefunction.config import Settings


def test_grants_are_stored_and_the_app_s_own_data_cannot_be_granted(tmp_path, monkeypatch) -> None:
    """Folder grants persist, while app storage and credentials remain protected."""
    isolated = main.Services(Settings(data_dir=tmp_path / "data", development_auth_bypass=True))
    monkeypatch.setattr(main, "services", isolated)

    with TestClient(main.app) as client:
        listed = client.get("/api/grants").json()
        assert listed["grants"] == []

        allowed = tmp_path / "documents"
        allowed.mkdir()
        added = client.post("/api/grants", json={"path": str(allowed), "writable": False})
        assert added.status_code == 201
        assert added.json()["grants"] == [{"path": str(allowed.resolve()), "writable": False}]

        # Re-granting the same folder replaces rather than duplicates, so read cannot silently
        # linger beside a later read-write grant for the same path.
        client.post("/api/grants", json={"path": str(allowed), "writable": True})
        grants = client.get("/api/grants").json()["grants"]
        assert grants == [{"path": str(allowed.resolve()), "writable": True}]

        agent_dir = isolated.settings.agent_dir
        agent_dir.mkdir(parents=True, exist_ok=True)
        repository = isolated.repository
        for forbidden in (agent_dir, agent_dir.parent, repository.skills.root, tmp_path):
            refused = client.post("/api/grants", json={"path": str(forbidden)})
            assert refused.status_code == 400, forbidden
            assert "app's own data" in refused.json()["detail"]

        missing = client.post("/api/grants", json={"path": str(tmp_path / "nope")})
        assert missing.status_code == 400

        client.request("DELETE", f"/api/grants?path={allowed}")
        assert client.get("/api/grants").json()["grants"] == []


@pytest.mark.skipif(sys.platform != "linux", reason="Linux's sandbox and AppArmor")
def test_settings_says_how_to_let_bwrap_start_where_apparmor_stops_it(tmp_path, monkeypatch) -> None:
    """Ubuntu 23.10 and later let only programs with an AppArmor profile make user namespaces, so
    bwrap refuses every command. Settings › Access then names the cause and the fix, and says no
    more than bwrap where AppArmor allows them."""
    from wavefunction import sandbox, sandbox_linux

    async def refused(self, command, **kwargs):
        return sandbox.CommandResult(1, "", "bwrap: setting up uid map: Permission denied\n")

    monkeypatch.setattr(sandbox_linux, "available", lambda: True)
    monkeypatch.setattr(sandbox.Sandbox, "run", refused)
    restricted = tmp_path / "apparmor_restrict_unprivileged_userns"
    monkeypatch.setattr(sandbox_linux, "APPARMOR_USERNS", restricted)

    restricted.write_text("1\n")
    problem = asyncio.run(sandbox.self_test(tmp_path / "scratch"))
    assert problem and "setting up uid map: Permission denied. AppArmor" in problem
    assert "`sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0`" in problem

    restricted.write_text("0\n")
    problem = asyncio.run(sandbox.self_test(tmp_path / "scratch"))
    assert problem and problem.endswith("setting up uid map: Permission denied")


def test_user_themes_load_from_the_themes_folder_with_assets_inlined(tmp_path, monkeypatch) -> None:
    """A theme's stylesheet comes back with its own assets inlined; one that reaches outside its
    folder, or has a broken manifest, is listed with the reason and its stylesheet is refused."""
    isolated = main.Services(Settings(data_dir=tmp_path / "data", development_auth_bypass=True))
    monkeypatch.setattr(main, "services", isolated)
    root = isolated.settings.data_dir / "themes"

    def theme(name: str, css: str, manifest: str = '{"name": "Glass"}') -> None:
        (root / name / "icons").mkdir(parents=True)
        (root / name / "theme.json").write_text(manifest)
        (root / name / "theme.css").write_text(css)
        (root / name / "icons" / "close.svg").write_text("<svg/>")

    theme(
        "glass",
        '.close { background: url("icons/close.svg"); } .again { mask: url(icons/close.svg#x); }',
        '{"name": "Glass", "appearances": ["light"], "windowControls": "theme"}',
    )
    theme("remote", ".a { background: url(https://example.com/a.png); }")
    theme("escape", ".a { background: url(../glass/icons/close.svg); }")
    theme("imports", '@import "other.css";')
    theme("broken", "", '{"appearances": ["sepia"]}')

    client = TestClient(main.app)
    listed = client.get("/api/themes").json()
    assert listed["folder"] == str(root)
    themes = {item["id"]: item for item in listed["themes"]}
    assert themes["glass"] == {
        "id": "glass",
        "name": "Glass",
        "description": "",
        "appearances": ["light"],
        "window_controls": "theme",
        "error": None,
    }
    assert "remote" in themes["remote"]["error"]
    assert "outside the theme's folder" in themes["escape"]["error"]
    assert "@import" in themes["imports"]["error"]
    assert 'needs a "name"' in themes["broken"]["error"]

    css = client.get("/api/themes/glass/stylesheet").json()["css"]
    data = "data:image/svg+xml;base64,PHN2Zy8+"
    assert css == f'.close {{ background: url("{data}"); }} .again {{ mask: url("{data}#x"); }}'
    refused = client.get("/api/themes/escape/stylesheet")
    assert refused.status_code == 422 and "outside" in refused.json()["detail"]
    assert client.get("/api/themes/missing/stylesheet").status_code == 404
