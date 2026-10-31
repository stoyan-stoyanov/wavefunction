"""Snapshots of real synthetic Git repositories. Nothing here deletes files."""

import pytest
from support import git

from wavefunction.coding_snapshots import capture


@pytest.fixture
def repo(tmp_path):
    folder = (tmp_path / "repo").resolve()
    folder.mkdir()
    git(folder, "init", "-q", "-b", "main")
    git(folder, "config", "user.name", "Synthetic Tester")
    git(folder, "config", "user.email", "synthetic@example.invalid")
    git(folder, "config", "commit.gpgsign", "false")
    (folder / "a.txt").write_text("one\n")
    git(folder, "add", ".")
    git(folder, "commit", "-qm", "Synthetic commit")
    return folder


def test_every_kind_of_change_reaches_the_next_snapshot(repo):
    first = capture(repo, "owner", "one")
    assert capture(repo, "owner", "same")["tree"] == first["tree"]

    (repo / "a.txt").write_text("two\n")
    edited = capture(repo, "owner", "edited")
    assert edited["tree"] != first["tree"]

    (repo / "new.txt").write_text("untracked\n")
    added = capture(repo, "owner", "added")
    assert added["tree"] != edited["tree"]

    (repo / "a.txt").chmod(0o755)
    made_executable = capture(repo, "owner", "executable")
    assert made_executable["tree"] != added["tree"]

    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic commit two")
    committed = capture(repo, "owner", "committed")
    assert committed["tree"] == made_executable["tree"]
    assert committed["commit"] != made_executable["commit"]
    assert committed["head"] == git(repo, "rev-parse", "HEAD").strip()

    for name, code in (("one", first), ("edited", edited), ("committed", committed)):
        assert git(repo, "rev-parse", f"refs/wavefunction/checkpoints/{name}").strip() == code["commit"]


def test_a_repository_with_no_files_can_be_saved(tmp_path):
    folder = (tmp_path / "empty").resolve()
    folder.mkdir()
    git(folder, "init", "-q", "-b", "main")
    git(
        folder,
        "-c",
        "user.name=S",
        "-c",
        "user.email=s@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Empty",
    )
    assert capture(folder, "owner", "empty")["tree"]


def test_an_unchanged_checkout_gets_a_new_ref_on_the_same_commit(repo):
    first = capture(repo, "owner", "one")
    second = capture(repo, "owner", "two")
    assert (second["tree"], second["commit"]) == (first["tree"], first["commit"])
    assert second["ref"] != first["ref"]
    assert git(repo, "rev-parse", second["ref"]).strip() == first["commit"]


def test_a_saved_commit_git_has_pruned_is_made_again(repo):
    first = capture(repo, "owner", "one")
    last = repo / ".git" / "wavefunction" / "owner.last.json"
    last.write_text(last.read_text().replace(first["commit"], "1" * 40))
    second = capture(repo, "owner", "two")
    assert second["tree"] == first["tree"]
    assert git(repo, "rev-parse", second["ref"]).strip() == second["commit"] != "1" * 40


def test_a_checkout_matching_its_source_leaves_no_ref_and_a_different_one_does(repo):
    code = capture(repo, "source", "code")
    matching = {"tree": code["tree"], "modes": code["modes"]}
    assert capture(repo, "new", "same", matching) == {"matches": True}
    assert "refs/wavefunction/checkpoints/same" not in git(repo, "for-each-ref")

    (repo / "a.txt").write_text("uncommitted\n")
    differs = capture(repo, "new", "edited", matching)
    assert differs["tree"] != code["tree"] and "matches" not in differs

    (repo / "a.txt").write_text("one\n")
    (repo / "a.txt").chmod(0o640)
    modes = capture(repo, "new", "modes", matching)
    assert modes["tree"] == code["tree"] and "matches" not in modes
