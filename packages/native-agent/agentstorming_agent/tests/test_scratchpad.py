# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""A scratchpad topic names one file inside the scratchpad, and nothing else."""

from __future__ import annotations

import pytest

from agentstorming_agent.scratchpad import ScratchpadStore


def test_a_topic_round_trips(tmp_path):
    sp = ScratchpadStore(tmp_path)
    sp.write("kv-cache_notes.v2", "paging, not compaction")
    assert sp.read("kv-cache_notes.v2") == "paging, not compaction"
    assert sp.list_topics() == ["kv-cache_notes.v2"]
    assert sp.read("never-written") == ""


@pytest.mark.parametrize("topic", [
    "../escape", "../../etc/passwd", "a/b", "/etc/passwd", "..\\x", "", ".hidden", "a b",
    "x" * 129,
])
def test_a_topic_that_is_a_path_is_refused_for_read_and_write(tmp_path, topic):
    sp = ScratchpadStore(tmp_path / "persona")
    outside = tmp_path / "escape.md"
    with pytest.raises(ValueError):
        sp.write(topic, "overwritten")
    with pytest.raises(ValueError):
        sp.read(topic)
    assert not outside.exists()


def test_a_symlink_out_of_the_scratchpad_is_refused(tmp_path):
    sp = ScratchpadStore(tmp_path / "persona")
    target = tmp_path / "outside.md"
    target.write_text("not the agent's")
    (sp.base / "link.md").symlink_to(target)
    with pytest.raises(ValueError):
        sp.write("link", "overwritten")
    with pytest.raises(ValueError):
        sp.read("link")
    assert target.read_text() == "not the agent's"


def test_the_prompt_never_includes_a_file_outside_the_scratchpad(tmp_path):
    """load_all_as_prompt feeds the system prompt and used to follow symlinks."""
    outside = tmp_path / "secret.md"
    outside.write_text("contents from outside the scratchpad")
    store = ScratchpadStore(tmp_path / "persona")
    store.write("notes", "kept")
    (store.base / "link.md").symlink_to(outside)
    prompt = store.load_all_as_prompt()
    assert "kept" in prompt and "outside the scratchpad" not in prompt
    assert store.list_topics() == ["notes"]


def test_a_legacy_topic_name_stays_in_the_prompt_but_is_not_offered(tmp_path):
    store = ScratchpadStore(tmp_path / "persona")
    (store.base / "kv cache.md").write_text("written before topics were checked")
    assert "written before" in store.load_all_as_prompt()
    assert store.list_topics() == []
