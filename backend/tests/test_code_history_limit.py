"""Code history's file limit, on real synthetic Git repositories."""

import pytest
from support import git

from wavefunction import coding_snapshots
from wavefunction.coding_snapshots import capture


@pytest.fixture
def limited(tmp_path, monkeypatch):
    monkeypatch.setattr(coding_snapshots, "MAX_FILES", 3)
    folder = (tmp_path / "repo").resolve()
    folder.mkdir()
    git(folder, "init", "-q", "-b", "main")
    git(folder, "config", "user.name", "Synthetic Tester")
    git(folder, "config", "user.email", "synthetic@example.invalid")
    git(folder, "config", "commit.gpgsign", "false")
    return folder


def commit(folder, *names):
    for name in names:
        (folder / name).write_text("synthetic\n")
    git(folder, "add", ".")
    git(folder, "commit", "-qm", "Synthetic commit")


def test_a_repository_at_the_limit_keeps_its_history(limited):
    commit(limited, "a.txt", "b.txt", "c.txt")
    assert capture(limited, "owner", "point")["tree"]


def test_a_repository_over_the_limit_is_refused_before_its_files_are_read(limited, monkeypatch):
    commit(limited, "a.txt", "b.txt", "c.txt", "d.txt")

    def read_a_file(_path):
        raise AssertionError("every file was checked before refusing")

    monkeypatch.setattr(coding_snapshots.Path, "is_symlink", read_a_file)
    with pytest.raises(ValueError, match="exceeds the code history limit"):
        capture(limited, "owner", "point")


def test_deleted_files_and_untracked_output_do_not_count(limited):
    commit(limited, "a.txt", "b.txt", "c.txt", "d.txt")
    (limited / "d.txt").unlink()
    (limited / "node_modules").mkdir()
    for index in range(5):
        (limited / "node_modules" / f"{index}.js").write_text("synthetic\n")
    assert capture(limited, "owner", "point")["tree"]


def test_files_a_sparse_checkout_leaves_out_do_not_count(limited):
    commit(limited, "a.txt", "b.txt", "c.txt", "d.txt")
    git(limited, "update-index", "--skip-worktree", "d.txt")
    (limited / "d.txt").unlink()
    assert capture(limited, "owner", "point")["tree"]
