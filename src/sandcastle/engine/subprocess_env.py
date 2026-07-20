"""Minimal, non-secret environments for child processes."""

from __future__ import annotations

import os

# Runtime plumbing only. Provider credentials and application configuration must
# be explicitly injected by each caller rather than inherited from the parent.
_SAFE_SUBPROCESS_ENV_VARS = frozenset(
    {
        "PATH",
        "NODE_PATH",
        "HOME",
        "USER",
        "LANG",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATHEXT",
    }
)


def build_minimal_subprocess_env() -> dict[str, str]:
    """Return OS plumbing needed by Python and Node child processes.

    Locale variables are retained as a family because POSIX permits several
    ``LC_*`` categories. All other variables, including parent credentials and
    application configuration, are intentionally excluded.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if value and (key in _SAFE_SUBPROCESS_ENV_VARS or key.startswith("LC_"))
    }
