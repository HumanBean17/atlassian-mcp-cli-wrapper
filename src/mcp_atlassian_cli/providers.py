"""Which mcp-atlassian provider distribution is installed, and what that means.

The provider ships via an install extra — ``[atlassian]`` (upstream
``mcp-atlassian``, Jira + Confluence) or ``[bitbucket]``
(``mcp-atlassian-with-bitbucket``, which adds Bitbucket) — and both publish
the SAME import package ``mcp_atlassian``, so the provider can only be told
apart by distribution metadata. Detection here is metadata-only: cheap enough
for every CLI run, and it never imports the server (which would read
credentials from the environment and cost seconds).
"""

from __future__ import annotations

from importlib import metadata

UPSTREAM_DISTRIBUTION = "mcp-atlassian"
FORK_DISTRIBUTION = "mcp-atlassian-with-bitbucket"

_BITBUCKET_HINTS: dict[str, str] = {
    # Each provider makes a different `atli bitbucket ...` failure meaningful:
    # upstream cannot mount Bitbucket at all (wrong provider installed), while
    # the fork can but was not given credentials.
    "atlassian": (
        "Bitbucket needs the [bitbucket] install extra "
        '(pip install "mcp-atlassian-cli[bitbucket]"); the two providers '
        "cannot coexist, so reinstall to switch (see the README)."
    ),
    "bitbucket": (
        "Bitbucket tools mount once BITBUCKET_URL and credentials are set "
        "(env or a profile) — run 'atli tools' to confirm."
    ),
}


def _dist_version(name: str) -> str:
    """Distribution version lookup (indirection for tests)."""
    return metadata.version(name)


def detect_provider() -> str | None:
    """The installed provider: ``"atlassian"``, ``"bitbucket"``, or ``None``.

    The fork wins if both distributions are somehow present: it is a superset
    of upstream's tool surface, so the Bitbucket-capable answer is the useful
    one even from a broken forced install.
    """
    for distribution, provider in (
        (FORK_DISTRIBUTION, "bitbucket"),
        (UPSTREAM_DISTRIBUTION, "atlassian"),
    ):
        try:
            _dist_version(distribution)
        # PackageNotFoundError is the documented "not installed" signal; the
        # others cover a corrupt .dist-info on disk, which must degrade to
        # the unknown-provider fallback (mute hints, generic messages) rather
        # than escape as a traceback — same defensiveness as the runner's
        # import guard.
        except (metadata.PackageNotFoundError, OSError, ValueError):
            continue
        return provider
    return None


def bitbucket_hint(provider: str | None) -> str | None:
    """Guidance for a Bitbucket command under ``provider``; ``None`` if mute.

    A bare install (``None``) stays silent: in production it never reaches
    the caller — the runner's provider-missing error fires first — and a hint
    computed from an impossible state would only mislead.
    """
    return _BITBUCKET_HINTS.get(provider)
