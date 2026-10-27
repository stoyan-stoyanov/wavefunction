from __future__ import annotations

import stat

import pytest

from wavefunction.secrets import EnvFileSecretStore


def test_env_file_secrets_persist_with_user_only_permissions_and_yield_to_the_environment(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "Agent" / ".wavefunction.env"
    store = EnvFileSecretStore(path)

    store.set("openrouter_api_key", "sk-or-v1-saved")
    store.set("tavily_api_key", "tvly-saved")

    assert EnvFileSecretStore(path).get("openrouter_api_key") == "sk-or-v1-saved"
    assert EnvFileSecretStore(path).get("tavily_api_key") == "tvly-saved"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8") == (
        "# Wavefunction local credentials. Keep this file private and out of Git.\n"
        'OPENROUTER_API_KEY="sk-or-v1-saved"\n'
        'TAVILY_API_KEY="tvly-saved"\n'
    )

    # A variable set in the app's environment wins over the saved file.
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment-value")
    assert store.get("openrouter_api_key") == "environment-value"
    monkeypatch.delenv("OPENROUTER_API_KEY")

    store.delete("openrouter_api_key")
    assert EnvFileSecretStore(path).get("openrouter_api_key") is None
    assert EnvFileSecretStore(path).get("tavily_api_key") == "tvly-saved"

    store.delete("tavily_api_key")
    assert not path.exists()


def test_a_connector_can_keep_its_oauth_tokens(tmp_path) -> None:
    """The model keys can be listed ahead of time; a connector's tokens cannot.

    The set is whatever the user has connected, so they are allowed by prefix. Still an
    allowlist — anything not named that way stays out of the credentials file.
    """
    from wavefunction.secrets import EnvFileSecretStore

    store = EnvFileSecretStore(tmp_path / ".wavefunction.env")

    store.set("mcp_oauth_tokens_agentcouch", '{"access_token": "abc"}')
    assert store.get("mcp_oauth_tokens_agentcouch") == '{"access_token": "abc"}'

    # A connector named with a dash is slugged before it reaches the store, so what goes in is
    # what comes back.
    from wavefunction.connector_oauth import token_key

    assert token_key("issue-tracker") != token_key("issue_tracker")
    store.set(token_key("issue-tracker"), '{"access_token": "x"}')
    assert store.get(token_key("issue-tracker")) == '{"access_token": "x"}'

    store.delete("mcp_oauth_tokens_agentcouch")
    assert store.get("mcp_oauth_tokens_agentcouch") is None

    # Anything else is still refused, and the message says which key.
    with pytest.raises(ValueError, match="unsupported secret key: whatever"):
        store.set("whatever", "x")
