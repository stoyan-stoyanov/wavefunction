"""Context windows come from the models.dev registry when the provider does not say."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_openai import ChatOpenAI

from wavefunction.execution_policy import context_window
from wavefunction.models import ModelGateway
from wavefunction.secrets import MemorySecretStore
from wavefunction.spec_common import ModelSpec


def test_the_gateway_takes_the_provider_figure_then_the_registry_then_the_bundled_profile() -> None:
    secrets = MemorySecretStore()
    secrets.set("openai_api_key", "test-key")
    live = {"openai": {"gpt-unreleased-test": {"context_length": 111}}}
    gateway = ModelGateway(secrets, capabilities=lambda: live, context_lengths=lambda _p, _m: 222)
    assert (
        gateway.chat_model(ModelSpec(provider="openai", model="gpt-unreleased-test")).profile["max_input_tokens"]
        == 111
    )

    gateway = ModelGateway(secrets, context_lengths=lambda _p, _m: 222)
    assert (
        gateway.chat_model(ModelSpec(provider="openai", model="gpt-unreleased-test")).profile["max_input_tokens"]
        == 222
    )

    gateway = ModelGateway(secrets)
    bundled = ChatOpenAI(model="gpt-5.4", api_key="test-key").profile["max_input_tokens"]
    assert (
        gateway.chat_model(ModelSpec(provider="openai", model="gpt-5.4")).profile["max_input_tokens"]
        == bundled
    )
    # Nobody has a figure: the window is assumed to be what current models take, not 32k.
    unknown = gateway.chat_model(ModelSpec(provider="openai", model="gpt-unreleased-test"))
    assert "max_input_tokens" not in (unknown.profile or {})
    assert context_window(unknown) == 128_000
    assert context_window(SimpleNamespace(profile=None)) == 128_000
