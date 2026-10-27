"""Plain helpers for the coding tests: a chat made through the API and a synthetic repository.

The `coding` fixture that serves the API is in conftest.py."""

import subprocess


async def create(client, **data):
    response = await client.post("/api/coding/threads", json=data)
    assert response.status_code == 201, response.text
    return response.json()


def git(folder, *args):
    return (
        subprocess.check_output(["git", "-C", str(folder), *args], stderr=subprocess.STDOUT).decode().strip()
    )


def repository(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    git(folder, "init", "-b", "main")
    git(folder, "config", "user.name", "Synthetic Tester")
    git(folder, "config", "user.email", "synthetic@example.invalid")
    (folder / "a.txt").write_text("before\n")
    (folder / "other.txt").write_text("other before\n")
    git(folder, "add", ".")
    git(folder, "commit", "-m", "Initial synthetic commit")
    return folder
