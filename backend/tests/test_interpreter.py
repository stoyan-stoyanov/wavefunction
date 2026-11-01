"""The engine is also the agent's `python3`, and every sandboxed file operation starts it."""

import subprocess
import sys


def test_the_engine_runs_a_script_file_as_its_python(tmp_path):
    script = tmp_path / "synthetic.py"
    script.write_text("print('ran')\n")
    result = subprocess.run(
        [sys.executable, "-m", "wavefunction", "--python", str(script)], capture_output=True, text=True
    )
    assert result.stdout == "ran\n", result.stderr


def test_acting_as_python_loads_none_of_the_server():
    code = "import sys; print(any(name in sys.modules for name in ('uvicorn', 'asyncio', 'argparse')))"
    result = subprocess.run(
        [sys.executable, "-m", "wavefunction", "--python", "-c", code], capture_output=True, text=True
    )
    assert result.stdout == "False\n", result.stderr
