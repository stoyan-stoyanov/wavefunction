"""Settings → Models: which image models a provider offers, and which are turned on."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from . import image_models
from . import responses as api
from .schemas import EnabledModelsUpdate


def register_image_settings(app, get_services, authorize) -> None:
    router = APIRouter(prefix="/api/connections/image-models", dependencies=[Depends(authorize)])

    @router.get("/{provider}", response_model=api.ImageModels)
    async def image_model_options(provider: str) -> dict[str, Any]:
        if provider not in image_models.PROVIDERS:
            raise HTTPException(404, f"{provider} has no image models")
        name = image_models.PROVIDERS[provider][0]
        try:
            return {"models": await image_models.list_models(provider, get_services().secrets)}
        except image_models.MissingKey as error:
            raise HTTPException(409, str(error)) from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise HTTPException(401, f"{name} rejected the saved API key") from error
            raise HTTPException(502, f"{name} could not list its image models") from error
        except httpx.HTTPError as error:
            raise HTTPException(502, f"Could not connect to {name}") from error

    @router.put("", response_model=api.EnabledImageModels)
    async def update_image_models(payload: EnabledModelsUpdate) -> dict[str, Any]:
        models = [
            key for key in dict.fromkeys(m.strip() for m in payload.models) if image_models.valid_key(key)
        ]
        get_services().repository.set_setting(image_models.SETTING, models)
        return {"models": models}

    app.include_router(router)
