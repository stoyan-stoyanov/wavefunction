"""The Vercel AI Gateway: a second route to the same models."""

from __future__ import annotations

from wavefunction import image_models
from wavefunction.vercel_gateway import VercelGatewayCatalog


def test_the_catalogue_keeps_language_models_and_drops_the_rest():
    """An embedding model cannot run an agent, and would only pad the picker. Image models are
    listed apart, for the agent's image tools."""
    catalogue = {
        "data": [
            {"id": "openai/gpt-5.6-luna", "name": "Luna", "type": "language", "context_window": 400000},
            {"id": "openai/text-embedding-3", "name": "Embed", "type": "embedding"},
            {"id": "black-forest/flux", "name": "Flux", "type": "image"},
        ]
    }
    parsed = VercelGatewayCatalog.parse_models(catalogue)

    assert [model.id for model in parsed] == ["openai/gpt-5.6-luna"]
    assert parsed[0].context_length == 400000
    assert image_models.parse_models("vercel", catalogue) == [{"id": "black-forest/flux", "name": "Flux"}]
