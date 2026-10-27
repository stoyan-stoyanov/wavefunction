"""Write the backend's OpenAPI schema to backend/openapi.json.

`npm run generate:api` runs this, then generates frontend/src/lib/api.gen.ts from the file.
The app is imported against a throwaway data folder, so nothing of the user's is read.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "backend" / "openapi.json"

with tempfile.TemporaryDirectory(prefix="wavefunction-openapi-") as data:
    os.environ["WAVEFUNCTION_DATA_DIR"] = data
    from wavefunction.main import app

    OUTPUT.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
