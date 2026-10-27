"""Models: hand-configured providers and model usage analytics."""

import json

import pytest
from support import create


async def test_manual_provider_configuration_survives_switches_null_defaults_and_key_removal(
    coding, tmp_path, monkeypatch
):
    client, ws, services = coding
    models = [
        {"provider": "openai-compatible", "model": "local-coder", "base_url": "http://127.0.0.1:1234/v1"},
        {"provider": "openai", "model": "custom-coder"},
    ]
    for model in models:
        response = await client.put("/api/coding/models", json=model)
        assert response.status_code == 200, response.text
        thread = await create(client)
        assert thread["model"]["provider"] == model["provider"]
        assert thread["model"]["model"] == model["model"]
        assert thread["model"]["base_url"] == model.get("base_url")
    assert len(services.repository.get_setting("coding_models")) == 2
    assert (await create(client))["model"]["model"] == "custom-coder"
    # Disabling the default falls back to the saved local model with its own server address.
    services.repository.set_setting("enabled_models", ["openai-compatible::local-coder"])
    fallback = (await create(client))["model"]
    assert (fallback["model"], fallback["base_url"]) == ("local-coder", "http://127.0.0.1:1234/v1")
    response = await client.put(
        "/api/coding/models",
        json={"provider": "openai-compatible", "model": "invalid", "base_url": "file:///repo"},
    )
    assert response.status_code == 422
    response = await client.put("/api/coding/models", json={"provider": "openai", "model": " "})
    assert response.status_code == 422
    # The key can be removed again, leaving the models configured with it. One set in the app's
    # environment wins over the saved file, so removing it says where it comes from instead.
    from wavefunction.secrets import EnvFileSecretStore

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(services, "secrets", EnvFileSecretStore(tmp_path / ".wavefunction.env"))
    services.secrets.set("openai_api_key", "sk-synthetic")
    assert (await client.get("/api/connections")).json()["secrets"]["openai_api_key"]
    assert (await client.delete("/api/connections/secrets/openai_api_key")).status_code == 204
    assert not (await client.get("/api/connections")).json()["secrets"]["openai_api_key"]
    assert len(services.repository.get_setting("coding_models")) == 2
    services.secrets.set("openai_api_key", "sk-saved")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-environment")
    refused = await client.delete("/api/connections/secrets/openai_api_key")
    assert refused.status_code == 409 and "OPENAI_API_KEY" in refused.json()["detail"]
    monkeypatch.delenv("OPENAI_API_KEY")
    assert services.secrets.get("openai_api_key") == "sk-saved"
    # Connector tokens are not Settings keys.
    assert (await client.delete("/api/connections/secrets/mcp_oauth_tokens_github")).status_code == 404


async def test_model_analytics_preserve_reported_usage_scope_dates_and_cache_coverage(coding, monkeypatch):
    from contextlib import contextmanager

    from wavefunction.repository import Repository

    client, ws, services = coding
    repo = services.repository
    monkeypatch.setattr("wavefunction.repository.now_iso", lambda: "2026-10-07T12:00:00+00:00")
    empty = (await client.get("/api/coding/analytics")).json()
    assert empty["models"] == [] and empty["totals"]["runs"] == 0
    assert empty["totals"]["total_tokens"] == {"value": 0, "known_runs": 0}
    assert len(empty["daily"]) == 14 and all(day["runs"] == 0 for day in empty["daily"])
    thread = await create(client)

    def record(
        provider,
        model,
        usage,
        *,
        status="completed",
        duration=1000,
        kind="user",
        started="2026-10-07T09:00:00Z",
        agent=None,
        calls=None,
    ):
        spec = {"model": {"provider": provider, "model": model}} if model else None
        run = repo.create_run(
            agent or ws.agent_id,
            {"text": "Synthetic request"},
            thread["conversation_id"],
            spec=spec,
            kind=kind,
        )
        repo.update_run(run["id"], status=status, duration_ms=duration, usage=usage)
        with repo.package(agent or ws.agent_id).connect() as db:
            db.execute("UPDATE runs SET started_at = ? WHERE id = ?", (started, run["id"]))
        if calls is not None:
            repo.add_run_event(run["id"], "model_call_finished", {"usage": calls})
        return run["id"]

    def usage(input_tokens, output_tokens, cached=None, cost=None):
        value = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        if cached is not None:
            value["input_token_details"] = {"cache_read": cached}
        if cost is not None:
            value["cost"] = cost
        return value

    complete = usage(100, 20, 40, 0.01)
    first = record("openrouter", "coder-a", complete, started="2026-10-06T23:30:00-02:00")
    repo.add_run_event(first, "model_call_failed", {"usage": [complete], "usage_status": "recovered"})
    # A helper's partial cache report is a reported subtotal, not an exact uncached count.
    record(
        "openrouter",
        "coder-a",
        usage(200, 30, 60),
        duration=3000,
        kind="subagent",
        calls=[usage(150, 20, 60), usage(50, 10)],
    )
    zero = usage(0, 0, 0, 0)
    record(
        "vercel",
        "coder-a",
        zero,
        status="failed",
        duration=2000,
        calls=[zero],
        started="2026-10-06T23:00:00Z",
    )
    record(None, None, {}, status="cancelled", duration=None, started="2026-09-20T09:00:00Z")
    active = record("openai", "coder-b", usage(50, 10), status="running", duration=500)
    repo.add_run_event(active, "summarization_finished", {"usage": usage(50, 10, 0)})
    repo.add_run_event(active, "model_call_failed", {"usage": [None], "usage_status": "unknown"})
    malformed = record(None, None, {}, status="failed", duration=-1)
    with repo.package(ws.agent_id).connect() as db:
        db.execute(
            "UPDATE runs SET spec_json = ?, usage_json = ? WHERE id = ?",
            ("legacy malformed spec", '{"input_tokens":true,"cost":-1}', malformed),
        )
        # Large/unparseable non-model payloads must not be read to compute statistics.
        db.execute(
            "INSERT INTO run_events VALUES (?, ?, 1, 'tool_completed', NULL, ?, ?)",
            ("synthetic-tool-event", malformed, "not JSON " + "x" * 100000, "2026-10-07"),
        )
    record("openrouter", "coder-a", complete, kind="fork", calls=[complete])
    unrelated = repo.create_agent("Unrelated", "Other product history")["id"]
    record("openrouter", "coder-a", usage(9000, 1000, 5000, 99), agent=unrelated)
    # Changing the chat's current model must not relabel its earlier model usage.
    response = await client.patch(
        f"/api/coding/threads/{thread['id']}", json={"model": {"provider": "openai", "model": "new-choice"}}
    )
    assert response.status_code == 200
    services.repository = Repository(repo.database_path, repo.agent_root)
    monkeypatch.setattr(
        "wavefunction.repository.hydrate", lambda *args: pytest.fail("Analytics hydrated a run")
    )
    monkeypatch.setattr(
        "wavefunction.repository.hydrate_all", lambda *args: pytest.fail("Analytics hydrated history")
    )
    statements = []
    original_history = services.repository._history

    @contextmanager
    def observed_history(project_id):
        with original_history(project_id) as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(services.repository, "_history", observed_history)
    response = await client.get("/api/coding/analytics")
    assert response.status_code == 200, response.text
    result = response.json()
    totals = result["totals"]
    assert {
        name: totals[name]
        for name in (
            "runs",
            "completed_runs",
            "failed_runs",
            "cancelled_runs",
            "active_runs",
            "user_requests",
        )
    } == {
        "runs": 6,
        "completed_runs": 2,
        "failed_runs": 2,
        "cancelled_runs": 1,
        "active_runs": 1,
        "user_requests": 5,
    }
    assert totals["input_tokens"] == {"value": 350, "known_runs": 4}
    assert totals["output_tokens"] == {"value": 60, "known_runs": 4}
    assert totals["total_tokens"] == {"value": 410, "known_runs": 4}
    assert totals["cached_input_tokens"] == {"value": 100, "known_runs": 3}
    assert totals["uncached_input_tokens"] == {"value": 60, "known_runs": 2}
    assert totals["cost_usd"] == {"value": 0.01, "known_runs": 2}
    assert totals["duration_ms"] == {"value": 6500, "known_runs": 4}
    assert totals["average_duration_ms"] == {"value": 1625, "known_runs": 4}
    assert totals["last_active_at"] == "2026-10-07T12:00:00.000Z"
    models = {(row["provider"], row["model"]): row for row in result["models"]}
    assert set(models) == {
        ("openrouter", "coder-a"),
        ("vercel", "coder-a"),
        ("openai", "coder-b"),
        (None, None),
    }
    assert models[(None, None)]["total_tokens"] == {"value": None, "known_runs": 0}
    assert models[("vercel", "coder-a")]["cost_usd"] == {"value": 0, "known_runs": 1}
    assert models[("openrouter", "coder-a")]["uncached_input_tokens"] == {"value": 60, "known_runs": 1}
    assert result["timezone"] == "UTC"
    assert result["daily"][0]["date"] == "2026-09-24"
    assert result["daily"][-1]["date"] == "2026-10-07"
    assert result["daily"][-1]["runs"] == 4 and result["daily"][-2]["runs"] == 1
    assert sum(day["runs"] for day in result["daily"]) == 5
    queries = [statement for statement in statements if statement.lstrip().startswith("WITH source")]
    assert len(queries) == 1
    with repo.package(ws.agent_id).connect() as db:
        plan = db.execute("EXPLAIN QUERY PLAN " + queries[0]).fetchall()
    assert "idx_events_type_time" in str([tuple(row) for row in plan])
    # Statistics are derived from durable records, without rewriting their usage/spec.
    assert repo.package(ws.agent_id).history_file.is_file()
    with repo.package(ws.agent_id).connect() as db:
        assert (
            json.loads(db.execute("SELECT usage_json FROM runs WHERE id = ?", (first,)).fetchone()[0])
            == complete
        )
