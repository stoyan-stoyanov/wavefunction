"""The task spec: what one coding task runs with, captured when it is accepted.

A task is a LangChain `create_agent` loop: a model calling tools until the task is complete. The
spec records the model, the system prompt, the tools (each allowed or withheld), the skills and
their pinned versions, and the repository folder. The tool registry stays closed; local command
execution is an explicit tool capability.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .spec_common import ModelSpec


class ToolPolicy(StrEnum):
    """Whether the model is offered a tool. A withheld tool is never registered, so the model
    never sees it and cannot be tempted by it."""

    ALLOW = "allow"
    DENY = "deny"


class ToolBinding(BaseModel):
    """One tool the task may call, and whether it is offered."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    policy: ToolPolicy = ToolPolicy.ALLOW


# `linear__create_issue`: a server name, two underscores, the tool's own name.
CONNECTOR_TOOL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}__[A-Za-z0-9_-]{1,64}$")
# The Agent Skills spec's own rule for a skill name.
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class AgentSpecV2(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    system_prompt: str = Field(min_length=1)

    # The repository folder a coding task captured when it was accepted. A queued or resumed
    # task keeps it even if its chat later moves to another folder.
    working_directory: str | None = Field(
        default=None, description="Absolute local repository folder, or ~/path. Does not grant file access."
    )

    @field_validator("working_directory")
    @classmethod
    def absolute_working_directory(cls, value):
        if value is not None and not (value.startswith("/") or value.startswith("~/")):
            raise ValueError("Repository folder must be an absolute path or start with ~/")
        return value

    model: ModelSpec = Field(default_factory=ModelSpec)
    tools: list[ToolBinding] = Field(default_factory=list)

    # Written guidance the agent may consult, by name. Only the name and description of each are
    # ever in the prompt; the body is read when the agent decides the skill applies. Validated by
    # shape, like a connector's tool, so a captured spec stays valid when a skill has been
    # deleted from the library.
    skills: list[str] = Field(default_factory=list)

    # Library skills, pinned to the version they had when the task started.
    skill_versions: dict[str, int] = Field(default_factory=dict)

    # Repository/global guidance is content-addressed in the library, so edits or removal
    # on disk cannot change a queued task or an approval resumed after a restart.
    skill_sources: dict[str, str] = Field(default_factory=dict)
    skill_paths: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_configuration(self) -> AgentSpecV2:
        from .toolkit import TOOL_REGISTRY

        self.skill_versions = {
            name: version for name, version in self.skill_versions.items() if name in self.skills
        }
        self.skill_sources = {
            name: digest for name, digest in self.skill_sources.items() if name in self.skills
        }
        self.skill_paths = {
            name: path for name, path in self.skill_paths.items() if name in self.skill_sources
        }
        if any(not re.fullmatch(r"[a-f0-9]{64}", digest) for digest in self.skill_sources.values()):
            raise ValueError("skill sources must be SHA-256 snapshot identifiers")
        if any(version < 1 for version in self.skill_versions.values()):
            raise ValueError("skill versions must be positive integers")

        if len(self.skills) != len(set(self.skills)):
            raise ValueError("skill names must be unique")
        malformed = [name for name in self.skills if not SKILL_NAME.match(name)]
        if malformed:
            raise ValueError(f"not a skill name: {', '.join(sorted(malformed))}")

        names = [binding.name for binding in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        # A connector's tool is `<server>__<tool>`, validated by shape rather than against the
        # servers connected right now: whether it can actually run is `build_tools`' problem.
        unknown = sorted(name for name in set(names) - set(TOOL_REGISTRY) if not CONNECTOR_TOOL.match(name))
        if unknown:
            available = ", ".join(sorted(TOOL_REGISTRY))
            raise ValueError(f"unknown tool(s): {', '.join(unknown)}. Available tools: {available}")
        return self

    def enabled_tools(self) -> list[ToolBinding]:
        """Tools the model will actually be offered. Denied tools are withheld entirely."""
        return [binding for binding in self.tools if binding.policy != ToolPolicy.DENY]


def load_spec(data: dict[str, Any]) -> AgentSpecV2:
    """Load a stored spec."""
    return AgentSpecV2.model_validate(data)
