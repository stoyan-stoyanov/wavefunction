from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .spec_common import ModelSpec


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    feedback: str = Field(default="", max_length=4000)
    expected_approval: int | None = Field(default=None, ge=1)


class ConnectionUpdate(BaseModel):
    model: ModelSpec


class EnabledModelsUpdate(BaseModel):
    models: list[str] = Field(default_factory=list)


class ZeroDataRetentionUpdate(BaseModel):
    enabled: bool


class NotificationSettings(BaseModel):
    desktop: bool
    input: bool
    sound: bool
    previews: bool


class SecretUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=10000)


class GrantCreate(BaseModel):
    """One outside folder the user makes available to coding tasks.

    Grants never come from a spec. A model may say an agent uses a write tool; it may never
    decide which paths that reaches.
    """

    path: str = Field(min_length=1, max_length=4096)
    writable: bool = False
