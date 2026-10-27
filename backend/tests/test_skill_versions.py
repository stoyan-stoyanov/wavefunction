import pytest

from wavefunction.skills import SkillLibrary, build_save_skill


def test_edits_preserve_pinned_guidance_and_deleted_skills(tmp_path):
    library = SkillLibrary(tmp_path)
    first = library.save("policy", "Refund guidance", "Old policy")
    second = library.save("policy", "Refund guidance", "New policy")
    assert (first.version, second.version) == (1, 2)
    assert library.select(["policy"], {"policy": 1})[0].body == "Old policy"
    library.delete("policy")
    assert library.get("policy") is None
    assert library.select(["policy"], {"policy": 1})[0].body == "Old policy"
    assert library.save("policy", "Refund guidance", "Third policy").version == 3
    # An agent's save never replaces a version its task did not start from.
    save = build_save_skill(library, {"policy": 3})
    library.save("policy", "Refund guidance", "Edited in Settings meanwhile")
    with pytest.raises(ValueError, match="changed after this task started"):
        save.invoke({"name": "policy", "description": "Refund guidance", "instructions": "Agent rewrite"})
    assert library.get("policy").body == "Edited in Settings meanwhile"
    library.delete("policy")
    with pytest.raises(ValueError, match="was deleted after this task started"):
        save.invoke({"name": "policy", "description": "Refund guidance", "instructions": "Agent rewrite"})
    assert library.get("policy") is None
    for body in ("First draft", "Second draft"):
        save.invoke({"name": "New Skill", "description": "Created by the agent", "instructions": body})
    assert library.get("new-skill").body == "Second draft"


def test_unversioned_skill_file_is_snapshotted_before_edit(tmp_path):
    library = SkillLibrary(tmp_path)
    path = library.path_for("policy")
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: policy\ndescription: Guidance\n---\nOld")
    assert library.save("policy", "Guidance", "New").version == 2
    assert library.select(["policy"], {"policy": 1})[0].body == "Old"
    assert library.select(["policy"], {})[0].body == "New"
    # A hand-edited file that no longer parses is left out of what tasks get, with a warning,
    # instead of being captured as a version that cannot be loaded.
    from wavefunction.skill_discovery import catalog, discover

    path.write_text("--\nname: policy\ndescription: Guidance\n---\nBroken fence")
    found = discover(None, library, grants=[], home=tmp_path / "home")
    # Still listed, so Settings can open and fix it.
    assert [
        (skill["name"], skill["active"]) for skill in found["skills"] if skill["source"] == "library"
    ] == [("policy", False)]
    assert any("could not be read" in warning for warning in found["warnings"])
    listed = catalog(library, [], grants=[], home=tmp_path / "home")["skills"]
    assert [(row["active"], row.get("overridden_by")) for row in listed if row["name"] == "policy"] == [
        (False, None)
    ]
    # Opened in Settings, it shows the instructions in the file, so fixing it neither starts from
    # nothing nor nests the broken frontmatter inside the new one; it needs a real description.
    assert library.get("policy").body == "Broken fence"
    assert next(skill.body for skill in library.list() if skill.name == "policy") == "Broken fence"
    with pytest.raises(ValueError, match="needs a description"):
        library.save("policy", library.get("policy").description, "Broken fence")
    path.write_text("---\nname: policy\ndescription: Guidance\nSteps:\n- No closing fence\n")
    assert library.get("policy").body == "Steps:\n- No closing fence"
    path.write_text(
        "---\nname: other\ndescription: >\n  Folded\n---\nReal steps\n"
    )  # Read as another skill's.
    assert library.get("policy").body == "Real steps"
    # The agent can repair it: no task was offered the broken file, so none read an older one.
    # A task that was given the skill and finds it broken since may not replace it.
    pinned = build_save_skill(library, {"policy": 1})
    with pytest.raises(ValueError, match="changed after this task started"):
        pinned.invoke({"name": "policy", "description": "Guidance", "instructions": "Stale rewrite"})
    build_save_skill(library, {}).invoke(
        {"name": "policy", "description": "Guidance", "instructions": "Fixed by the agent"}
    )
    assert library.get("policy").body == "Fixed by the agent"
    # A mark some editors put at the start of UTF-8 is read past; a file that is not UTF-8 is not
    # replaced by a save that could show none of it.
    path.write_bytes(b"\xef\xbb\xbf---\nname: policy\ndescription: Guidance\n---\nWith a mark\n")
    assert library.get("policy").body == "With a mark"
    path.write_bytes(b"---\nname: policy\ndescription: Guidance\n---\n\x93Quoted\x94\n")
    with pytest.raises(ValueError, match="not UTF-8"):
        library.save("policy", "Guidance", "")
    assert path.read_bytes().endswith(b"\x93Quoted\x94\n")
