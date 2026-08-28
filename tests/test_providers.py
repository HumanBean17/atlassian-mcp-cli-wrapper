"""Tests for provider-distribution detection and its guidance messages."""

from __future__ import annotations

import sys

import pytest
from pytest import MonkeyPatch

from mcp_atlassian_cli import providers


def patch_dists(monkeypatch: MonkeyPatch, dists: dict[str, str]) -> None:
    """Point ``_dist_version`` at a fake distribution table.

    CI installs exactly one provider per leg (and a developer checkout may
    have either), so detection must be tested against a fabricated metadata
    state — never the ambient one.
    """

    def fake(name: str) -> str:
        if name not in dists:
            raise providers.metadata.PackageNotFoundError(name)
        return dists[name]

    monkeypatch.setattr(providers, "_dist_version", fake)


def test_detect_fork_when_both_present(monkeypatch: MonkeyPatch) -> None:
    """A forced both-distributions install reports the fork: it is a superset
    of upstream's surface, so the Bitbucket-capable answer is the useful one."""
    patch_dists(
        monkeypatch,
        {
            providers.UPSTREAM_DISTRIBUTION: "0.23.0",
            providers.FORK_DISTRIBUTION: "1.0.5",
        },
    )
    assert providers.detect_provider() == "bitbucket"


def test_detect_upstream(monkeypatch: MonkeyPatch) -> None:
    patch_dists(monkeypatch, {providers.UPSTREAM_DISTRIBUTION: "0.23.0"})
    assert providers.detect_provider() == "atlassian"


def test_detect_fork(monkeypatch: MonkeyPatch) -> None:
    patch_dists(monkeypatch, {providers.FORK_DISTRIBUTION: "1.0.5"})
    assert providers.detect_provider() == "bitbucket"


def test_detect_bare_install(monkeypatch: MonkeyPatch) -> None:
    patch_dists(monkeypatch, {})
    assert providers.detect_provider() is None


def test_hint_upstream_names_the_extra() -> None:
    """The wrong-provider failure must teach the fix: the [bitbucket] extra,
    and that switching providers means a reinstall (they cannot coexist)."""
    hint = providers.bitbucket_hint("atlassian")
    assert hint is not None
    assert "mcp-atlassian-cli[bitbucket]" in hint
    assert "cannot coexist" in hint


def test_hint_fork_names_the_env_vars() -> None:
    """The fork-installed failure is unconfigured access, not a missing
    install — the hint points at BITBUCKET_* credentials."""
    hint = providers.bitbucket_hint("bitbucket")
    assert hint is not None
    assert "BITBUCKET_URL" in hint
    assert "pip install" not in hint


def test_hint_bare_install_is_mute() -> None:
    """A bare install never reaches the hint in production (the runner's
    provider-missing error fires first); computing one anyway would mislead."""
    assert providers.bitbucket_hint(None) is None


@pytest.mark.parametrize(
    "dists",
    [
        {providers.UPSTREAM_DISTRIBUTION: "0.23.0"},
        {providers.FORK_DISTRIBUTION: "1.0.5"},
        {
            providers.UPSTREAM_DISTRIBUTION: "0.23.0",
            providers.FORK_DISTRIBUTION: "1.0.5",
        },
        {},
    ],
    ids=["upstream", "fork", "both", "bare"],
)
def test_detect_provider_is_metadata_only(
    monkeypatch: MonkeyPatch, dists: dict[str, str]
) -> None:
    """Detection must never import the server: it runs before the first tool
    call on EVERY CLI invocation, and importing mcp_atlassian reads
    credentials from the environment and costs seconds.

    The proof is the sys.modules SNAPSHOT comparison — detection added
    nothing — not an ambient ``"mcp_atlassian" not in sys.modules`` check:
    other tests in this process legitimately import mcp_atlassian (the runner
    suite does), and an ambient assertion would fail merely from collection
    order. Parametrized across the loop's branches (fork first, then
    upstream, then the bare fallback)."""
    patch_dists(monkeypatch, dists)
    before = set(sys.modules)
    providers.detect_provider()
    assert set(sys.modules) == before


def test_hints_are_single_sentences() -> None:
    """Hints append to an existing one-line error; multi-line output would
    break the one-clean-line-per-error stderr contract."""
    for provider in ("atlassian", "bitbucket"):
        hint = providers.bitbucket_hint(provider)
        assert hint is not None
        assert "\n" not in hint
