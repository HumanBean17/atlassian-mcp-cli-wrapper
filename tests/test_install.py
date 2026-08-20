"""Tests for the prime hook installer (merge, never clobber)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_atlassian_cli.config import ConfigError
from mcp_atlassian_cli.install import HOOK_COMMAND, HARNESSES, install, merge_hook, run_install


def test_merge_hook_into_empty_settings() -> None:
    merged, changed = merge_hook({})
    assert changed is True
    assert merged == {
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": HOOK_COMMAND}]}]}
    }


def test_merge_hook_is_idempotent() -> None:
    once, changed = merge_hook({})
    assert changed is True
    twice, changed_again = merge_hook(once)
    assert changed_again is False
    assert twice == once


def test_merge_hook_preserves_unrelated_keys_and_order() -> None:
    existing = {
        "model": "opus",
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "other"}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": "greet"}]}],
        },
    }
    merged, changed = merge_hook(existing)

    assert changed is True
    assert list(merged) == ["model", "hooks"]  # key order preserved
    assert merged["model"] == "opus"
    assert merged["hooks"]["PreToolUse"] == existing["hooks"]["PreToolUse"]
    session = merged["hooks"]["SessionStart"]
    assert session[0] == {"hooks": [{"type": "command", "command": "greet"}]}
    assert session[1]["hooks"][-1]["command"] == HOOK_COMMAND  # appended, not replaced


def test_merge_hook_rejects_malformed_hooks_shape() -> None:
    with pytest.raises(ConfigError, match="SessionStart"):
        merge_hook({"hooks": {"SessionStart": "not-a-list"}})
    with pytest.raises(ConfigError, match="hooks"):
        merge_hook({"hooks": "not-a-dict"})


def test_install_creates_missing_settings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    message = install("claude", "user", home=home, cwd=tmp_path)

    assert message.startswith("installed:")
    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["hooks"]["SessionStart"][0]["hooks"][0]["command"] == HOOK_COMMAND


def test_install_second_run_reports_already(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = install("claude", "user", home=home, cwd=tmp_path)
    second = install("claude", "user", home=home, cwd=tmp_path)

    assert first.startswith("installed:")
    assert second.startswith("already installed:")


def test_install_corrupt_settings_aborts_without_write(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    corrupt = '{"broken":'
    settings_path.write_text(corrupt, encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid JSON"):
        install("claude", "user", home=home, cwd=tmp_path)

    assert settings_path.read_text(encoding="utf-8") == corrupt  # untouched


def test_install_non_object_json_aborts_without_write(tmp_path: Path) -> None:
    """Valid JSON that is not an object (a hand-mangled file) is a clean
    ConfigError — never an AttributeError traceback."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigError, match="top-level JSON must be an object"):
        install("claude", "user", home=home, cwd=tmp_path)

    assert settings_path.read_text(encoding="utf-8") == "[]"


def test_merge_hook_skips_junk_entries_tolerantly() -> None:
    """Non-dict entries and non-list hooks inside SessionStart are skipped,
    not fatal — the file works for its owner, so it works for us."""
    settings = {"hooks": {"SessionStart": ["junk", 3, {"hooks": 5}]}}
    merged, changed = merge_hook(settings)
    assert changed is True
    assert merged["hooks"]["SessionStart"][-1]["hooks"][-1]["command"] == HOOK_COMMAND


def test_install_is_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    """The write goes through a sibling tmp + rename, so no .tmp file
    survives a successful install."""
    home = tmp_path / "home"

    install("claude", "user", home=home, cwd=tmp_path)

    claude_dir = home / ".claude"
    assert sorted(p.name for p in claude_dir.iterdir()) == ["settings.json"]


def test_run_install_dedupes_harness_names(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)

    messages = run_install(["claude", "claude"], "user", home=home, cwd=tmp_path)

    assert len(messages) == 1


def test_unsupported_harness_reports_without_touching_disk(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    message = install("gemini", "user", home=home, cwd=tmp_path)

    assert message.startswith("not supported yet: gemini")
    assert list(home.iterdir()) == []  # nothing written


def test_run_install_detects_installed_harnesses(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    # .codex absent, .gemini absent

    messages = run_install(None, "user", home=home, cwd=tmp_path)

    assert len(messages) == 1
    assert messages[0].startswith("installed:")


def test_run_install_nothing_detected_teaches_instead_of_silence(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()

    messages = run_install(None, "user", home=home, cwd=tmp_path)

    assert len(messages) == 1
    assert messages[0].startswith("no harnesses detected")
    assert "--harness" in messages[0]


def test_run_install_reports_unsupported_harnesses(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".gemini").mkdir(parents=True)

    messages = run_install(None, "user", home=home, cwd=tmp_path)

    assert len(messages) == 2
    assert any(m.startswith("installed:") for m in messages)
    assert any(m.startswith("not supported yet: gemini") for m in messages)


def test_run_install_unknown_harness_names_everything(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        run_install(["bogus"], "user", home=tmp_path, cwd=tmp_path)

    assert "claude" in str(excinfo.value)
    assert "gemini" in str(excinfo.value)
    assert "codex" in str(excinfo.value)


def test_install_project_scope_writes_cwd(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    install("claude", "project", home=home, cwd=project)

    settings = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["hooks"]["SessionStart"][0]["hooks"][0]["command"] == HOOK_COMMAND


def test_harness_registry_covers_all_three() -> None:
    assert set(HARNESSES) == {"claude", "gemini", "codex"}
    assert HARNESSES["claude"].supported is True
    assert HARNESSES["gemini"].supported is False
    assert HARNESSES["codex"].supported is False
