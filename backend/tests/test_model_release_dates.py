"""The lists people choose models from say when each was released, from the models.dev registry."""

from __future__ import annotations

import json

from wavefunction.model_registry import ModelRegistry


async def test_a_provider_list_carries_the_registry_release_dates(coding) -> None:
    client, _workspace, services = coding
    snapshot = {
        "openrouter": {
            "models": {
                "vendor/dated": {"release_date": "2026-10-26"},
                "vendor/to-the-month": {"release_date": "2025-09"},
            }
        }
    }

    async def fetch() -> bytes:
        return json.dumps({**snapshot, "openai": {"models": {"m": {"limit": {"context": 1}}}}}).encode()

    assert await services.model_registry.refresh(fetch)
    listed = [
        {
            "id": model,
            "name": model,
            "description": "",
            "context_length": 0,
            "input_modalities": [],
            "output_modalities": [],
            "supported_parameters": [],
            "pricing": {},
            "reasoning_efforts": None,
        }
        for model in ("vendor/dated:free", "vendor/to-the-month", "vendor/unknown")
    ]

    async def list_models() -> list[dict]:
        return listed

    services.openrouter.list_models = list_models
    response = await client.get("/api/openrouter/models")
    dates = {model["id"]: model["release_date"] for model in response.json()["models"]}
    assert dates == {"vendor/dated:free": "2026-10-26", "vendor/to-the-month": None, "vendor/unknown": None}
    # The next launch reads them from the snapshot on disk.
    assert ModelRegistry(services.model_registry.path).dates == services.model_registry.dates
