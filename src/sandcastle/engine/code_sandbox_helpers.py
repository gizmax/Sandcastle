"""File helpers for the in-process code-step sandbox.

This module imports ONLY ``base64`` and ``pathlib`` on purpose. The helper
closures returned by :func:`make_code_sandbox_helpers` are injected into the
restricted ``exec`` globals of a code step. Because they are defined here and
not in ``executor``, their ``__globals__`` contains no application secrets
(``settings``, ``async_session``, database engines, etc.). This closes the
format-string / ``__globals__`` traversal vector where a crafted code step
reaches back into the defining module's namespace through a helper function.

The fully robust isolation is still the out-of-process sandbox backend; this is
one of several defense-in-depth mitigations layered on top of the admin gate.
"""

import base64
from pathlib import Path
from typing import Callable


def make_code_sandbox_helpers(
    data_dir: Path,
) -> tuple[Callable[[str], str], Callable[[str, str], str]]:
    """Build the ``read_file_b64`` / ``save_file_b64`` helpers bound to ``data_dir``.

    Returns a ``(read_file_b64, save_file_b64)`` tuple. Both helpers confine all
    filesystem access to ``data_dir`` and raise ``PermissionError`` on escape.
    """
    resolved_data_dir = Path(data_dir).resolve()

    def read_file_b64(file_path: str) -> str:
        """Read a file from disk and return its base64-encoded content."""
        p = Path(file_path).resolve()
        if not p.is_relative_to(resolved_data_dir):
            raise PermissionError(f"Access denied: {file_path} is outside data directory")
        if not p.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        return base64.b64encode(p.read_bytes()).decode("ascii")

    def save_file_b64(file_path: str, dest_name: str) -> str:
        """Read a file, base64-encode it, save to a temp file, return the temp path.

        Use this instead of read_file_b64 when the base64 would be too large
        to store in the workflow context (>5MB). The returned path can be used
        with {file:/path} syntax in HTTP step bodies for lazy loading.
        """
        p = Path(file_path).resolve()
        if not p.is_relative_to(resolved_data_dir):
            raise PermissionError(f"Access denied: {file_path} is outside data directory")
        if not p.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        b64_data = base64.b64encode(p.read_bytes()).decode("ascii")
        # Confine the destination to the tmp dir (subdirs allowed) so a crafted
        # dest_name like "../../.ssh/authorized_keys" cannot escape it.
        tmp_dir = (resolved_data_dir / "tmp").resolve()
        dest = (resolved_data_dir / "tmp" / dest_name).resolve()
        if not dest.is_relative_to(tmp_dir):
            raise PermissionError(
                f"Access denied: destination {dest_name} escapes the tmp directory"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(b64_data)
        return f"@file:{dest}"

    return read_file_b64, save_file_b64
