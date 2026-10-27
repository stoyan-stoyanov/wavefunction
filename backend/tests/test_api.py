from __future__ import annotations

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
