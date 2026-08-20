"""SessionStart hook installer for ``atli prime --install``.

Onboarding is the cold-start bottleneck: without a hook, agents never hear
of atli. This module merges the prime hook into existing harness settings —
merge, never clobber — so one command onboards a machine or project.

Harness support matrix (verified 2026-08): Claude Code's SessionStart hook
injects ``additionalContext`` and is supported. Gemini CLI runs SessionStart
hooks but does not inject their context (gemini-cli issue #15413). Codex
hooks sit behind the experimental ``[features] codex_hooks`` flag with known
firing issues. Unsupported harnesses report themselves and touch nothing;
their registry entries carry the settings paths so enabling them later is a
data change, not new code.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from mcp_atlassian_cli.config import ConfigError

HOOK_COMMAND = "atli prime --hook-json"
"""The hook entry every harness installs; also the idempotency key."""


@dataclass(frozen=True)
class Harness:
    """One agent harness atli can prime, or knows it cannot (yet)."""

    name: str
    detect_dir_name: str
    """Config dir under HOME whose presence means "this harness is used"."""

    settings_relpaths: dict[str, str]
    """scope -> settings path, relative to HOME for ``user`` and cwd for
    ``project``."""

    supported: bool
    note: str = ""
    """Why the harness is unsupported; shown in the report line."""


HARNESSES: dict[str, Harness] = {
    "claude": Harness(
        name="claude",
        detect_dir_name=".claude",
        settings_relpaths={
            "user": ".claude/settings.json",
            "project": ".claude/settings.json",
        },
        supported=True,
    ),
    "gemini": Harness(
        name="gemini",
        detect_dir_name=".gemini",
        settings_relpaths={
            "user": ".gemini/settings.json",
            "project": ".gemini/settings.json",
        },
        supported=False,
        note="SessionStart additionalContext is not injected upstream "
        "(gemini-cli issue #15413)",
    ),
    "codex": Harness(
        name="codex",
        detect_dir_name=".codex",
        settings_relpaths={
            "user": ".codex/config.toml",
            "project": ".codex/config.toml",
        },
        supported=False,
        note="hooks are experimental behind [features] codex_hooks",
    ),
}


def merge_hook(settings: dict) -> tuple[dict, bool]:
    """Merge the SessionStart hook into parsed settings; ``changed`` is False
    when it is already present (idempotent by construction).

    Only the hook entry is appended: every other key, list, and ordering
    survives untouched. A malformed ``hooks``/``SessionStart`` shape raises
    rather than guessing how to repair someone's hand-written settings;
    junk INSIDE the SessionStart list (non-dict entries, non-list ``hooks``)
    is skipped tolerantly — the file works for its owner, so it works for us.
    """
    hooks = settings.get("hooks")
    if hooks is None:
        hooks = {}
    if not isinstance(hooks, dict):
        raise ConfigError("settings 'hooks' must be a table")
    session = hooks.get("SessionStart")
    if session is None:
        session = []
    if not isinstance(session, list):
        raise ConfigError("settings 'hooks.SessionStart' must be a list")
    for entry in session:
        if not isinstance(entry, dict):
            continue
        entry_hooks = entry.get("hooks", ())
        if not isinstance(entry_hooks, (list, tuple)):
            continue
        for hook in entry_hooks:
            if isinstance(hook, dict) and hook.get("command") == HOOK_COMMAND:
                return settings, False
    merged = dict(settings)
    merged_hooks = dict(hooks)
    merged_session = list(session)
    merged_session.append({"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    merged_hooks["SessionStart"] = merged_session
    merged["hooks"] = merged_hooks
    return merged, True


def _settings_path(harness: Harness, scope: str, home: Path, cwd: Path) -> Path:
    base = home if scope == "user" else cwd
    return base / harness.settings_relpaths[scope]


def install(name: str, scope: str, *, home: Path, cwd: Path) -> str:
    """Install the hook for one harness; returns the report line.

    Unsupported harnesses are reported, never touched. A settings file that
    exists but does not parse aborts with no write — corrupting a user's
    settings to save them a manual edit would be a bad trade.
    """
    harness = HARNESSES[name]
    if not harness.supported:
        return f"not supported yet: {name} — {harness.note}"
    path = _settings_path(harness, scope, home, cwd)
    settings: dict = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ConfigError(
                f"{path}: not valid JSON ({error}). Fix or remove the file, "
                "then re-run."
            ) from error
        if not isinstance(settings, dict):
            raise ConfigError(
                f"{path}: top-level JSON must be an object, got "
                f"{type(settings).__name__}. Fix or remove the file, then "
                "re-run."
            )
    merged, changed = merge_hook(settings)
    if not changed:
        return f"already installed: {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic swap: write the sibling temp file, then rename over the target —
    # a crash mid-write can never truncate the user's settings (the very
    # corruption the read path refuses to tolerate).
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp_path, path)
    return f"installed: {path} (SessionStart += {HOOK_COMMAND})"


def run_install(
    names: list[str] | None, scope: str, *, home: Path, cwd: Path
) -> list[str]:
    """Resolve the harness list and install each; one report line per harness.

    ``names is None`` means auto-detect: every harness whose config dir
    exists under ``home``. Explicit names install regardless of detection
    (the user knows better than the filesystem).
    """
    if names is None:
        selected = [
            harness.name
            for harness in HARNESSES.values()
            if (home / harness.detect_dir_name).exists()
        ]
        if not selected:
            return [
                "no harnesses detected — install one (e.g. Claude Code), or "
                "pass --harness NAME explicitly"
            ]
    else:
        unknown = [name for name in names if name not in HARNESSES]
        if unknown:
            valid = ", ".join(sorted(HARNESSES))
            raise ConfigError(f"Unknown harness: {', '.join(unknown)}. Valid: {valid}.")
        selected = list(dict.fromkeys(names))  # dedupe, keep order
    return [install(name, scope, home=home, cwd=cwd) for name in selected]
