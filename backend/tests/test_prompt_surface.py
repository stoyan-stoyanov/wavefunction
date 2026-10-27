"""The committed prompt surface is what a default chat sends the model."""

import difflib

import pytest
from prompt_surface import COMMAND, FIXTURE, capture


@pytest.mark.sandbox  # The repository's AGENTS.md is read by the sandboxed file helper.
async def test_committed_prompt_surface_matches_a_default_chats_first_request(tmp_path):
    current = await capture(tmp_path.resolve())
    saved = FIXTURE.read_text(encoding="utf-8") if FIXTURE.exists() else ""
    if current != saved:
        changed = "".join(
            list(
                difflib.unified_diff(
                    saved.splitlines(keepends=True),
                    current.splitlines(keepends=True),
                    "committed",
                    "current",
                    n=1,
                )
            )[:60]
        )
        pytest.fail(f"{FIXTURE.name} is out of date. Run `{COMMAND}` and review the change:\n{changed}")
