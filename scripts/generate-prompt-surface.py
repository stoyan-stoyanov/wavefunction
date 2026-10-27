"""Write backend/tests/fixtures/prompt_surface/default_chat.md: what a default chat sends the model.

`npm run generate:prompt-surface` runs this; tests/test_prompt_surface.py fails until the file
matches. The app runs in a throwaway folder, against a scripted model, with gh signed out.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1] / "backend" / "tests"

with tempfile.TemporaryDirectory(prefix="wavefunction-prompt-surface-") as folder:
    root = Path(folder).resolve()
    os.environ["WAVEFUNCTION_DATA_DIR"] = str(root / "import")
    sys.path.insert(0, str(TESTS))
    from prompt_surface import FIXTURE, capture

    text = asyncio.run(capture(root))
FIXTURE.parent.mkdir(parents=True, exist_ok=True)
FIXTURE.write_text(text, encoding="utf-8")
print(f"Wrote {FIXTURE.relative_to(TESTS.parents[1])}")
