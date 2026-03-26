"""Storage backends for persistent data between workflow runs."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# Maximum content size for a single write operation (10 MB)
_MAX_WRITE_SIZE = 10 * 1024 * 1024

# Maximum S3 key length (AWS limit is 1024 bytes)
_MAX_S3_KEY_LENGTH = 1024

# Control characters (C0 range except tab/newline/carriage-return, plus DEL)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _validate_key_common(key: str, label: str = "key") -> None:
    """Shared validation for storage keys / paths."""
    if not key or not key.strip():
        raise ValueError(f"{label} must not be empty")
    if _CONTROL_CHAR_RE.search(key):
        raise ValueError(f"{label} must not contain control characters")


class StorageBackend(Protocol):
    """Protocol for storage backend implementations."""

    async def read(self, path: str) -> str | None: ...
    async def write(self, path: str, content: str) -> None: ...
    async def list(self, prefix: str) -> list[str]: ...
    async def delete(self, path: str) -> None: ...


class LocalStorage:
    """Filesystem-based storage backend for local development."""

    def __init__(self, base_dir: str = "./data/storage") -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, path: str) -> Path:
        """Resolve path and ensure it stays within base_dir.

        Also rejects null bytes and control characters which could confuse
        filesystem APIs or logging.
        """
        _validate_key_common(path, "Storage path")
        resolved = (self.base_dir / path).resolve()
        if not resolved.is_relative_to(self.base_dir):
            raise ValueError(f"Path traversal denied: {path}")
        # Reject symlinks that resolve outside base_dir (defense-in-depth)
        if resolved.is_symlink():
            real = resolved.resolve(strict=False)
            if not real.is_relative_to(self.base_dir):
                raise ValueError(f"Symlink traversal denied: {path}")
        return resolved

    async def read(self, path: str) -> str | None:
        """Read content from a file."""
        file_path = self._safe_path(path)
        if not file_path.exists():
            return None
        # Guard against reading symlinks that point outside base_dir
        if file_path.is_symlink():
            real = file_path.resolve()
            if not real.is_relative_to(self.base_dir):
                raise ValueError(f"Symlink traversal denied: {path}")
        return file_path.read_text(encoding="utf-8")

    async def write(self, path: str, content: str) -> None:
        """Write content to a file atomically (write-to-temp-then-rename).

        Size limit is enforced on the UTF-8 encoded byte length, not the
        character count, to prevent oversized writes from multi-byte content.
        """
        byte_len = len(content.encode("utf-8"))
        if byte_len > _MAX_WRITE_SIZE:
            raise ValueError(
                f"Content too large for storage write "
                f"({byte_len} bytes, max {_MAX_WRITE_SIZE})"
            )
        file_path = self._safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(file_path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(file_path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def list(self, prefix: str) -> list[str]:
        """List files matching a prefix.

        An empty prefix lists all files under the base directory.
        """
        if not prefix:
            search_dir = self.base_dir
        else:
            safe_base = self._safe_path(prefix)
            search_dir = safe_base.parent if not safe_base.is_dir() else safe_base
        if not search_dir.exists():
            return []
        results: list[str] = []
        for p in search_dir.rglob("*"):
            if p.is_file() and not p.is_symlink():
                rel = str(p.relative_to(self.base_dir))
                if rel.startswith(prefix):
                    results.append(rel)
        return sorted(results)

    async def delete(self, path: str) -> None:
        """Delete a file."""
        file_path = self._safe_path(path)
        if file_path.exists():
            file_path.unlink()



class S3Storage:
    """S3-compatible storage backend (works with MinIO)."""

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ) -> None:
        if not bucket or not bucket.strip():
            raise ValueError("S3 bucket name must not be empty")
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        # Reuse a single session across all operations to avoid resource leaks
        import aioboto3

        self._session = aioboto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )

    @staticmethod
    def _safe_key(key: str) -> str:
        """Sanitize S3 key to prevent path traversal, control chars, and
        enforce the AWS 1024-byte key length limit."""
        _validate_key_common(key, "S3 key")
        # Enforce AWS key length limit
        if len(key.encode("utf-8")) > _MAX_S3_KEY_LENGTH:
            raise ValueError(
                f"S3 key exceeds maximum length ({_MAX_S3_KEY_LENGTH} bytes)"
            )
        # Normalize path traversal sequences
        import posixpath
        normalized = posixpath.normpath(key)
        # Reject keys that escape the root after normalization
        if normalized.startswith("..") or normalized.startswith("/"):
            raise ValueError(f"S3 key traversal denied: {key}")
        return normalized

    def _get_session(self):
        """Return the shared aioboto3 session."""
        return self._session

    async def read(self, path: str) -> str | None:
        """Read content from S3."""
        safe = self._safe_key(path)
        session = self._get_session()
        async with session.client("s3", endpoint_url=self.endpoint_url) as s3:
            try:
                resp = await s3.get_object(Bucket=self.bucket, Key=safe)
                body = await resp["Body"].read()
                return body.decode("utf-8")
            except s3.exceptions.NoSuchKey:
                return None
            except Exception as e:
                logger.error("S3 read error for '%s': %s", safe, e)
                raise

    async def write(self, path: str, content: str) -> None:
        """Write content to S3.

        Size limit is enforced on the UTF-8 encoded byte length.
        """
        safe = self._safe_key(path)
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_WRITE_SIZE:
            raise ValueError(
                f"Content too large for storage write "
                f"({len(encoded)} bytes, max {_MAX_WRITE_SIZE})"
            )
        session = self._get_session()
        async with session.client("s3", endpoint_url=self.endpoint_url) as s3:
            await s3.put_object(
                Bucket=self.bucket,
                Key=safe,
                Body=encoded,
                ContentType="application/json",
            )

    async def list(self, prefix: str) -> list[str]:
        """List objects matching a prefix."""
        safe = self._safe_key(prefix) if prefix else ""
        session = self._get_session()
        async with session.client("s3", endpoint_url=self.endpoint_url) as s3:
            results: list[str] = []
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=safe):
                for obj in page.get("Contents", []):
                    results.append(obj["Key"])
            return results

    async def delete(self, path: str) -> None:
        """Delete an object from S3."""
        safe = self._safe_key(path)
        session = self._get_session()
        async with session.client("s3", endpoint_url=self.endpoint_url) as s3:
            await s3.delete_object(Bucket=self.bucket, Key=safe)


def create_storage() -> LocalStorage | S3Storage:
    """Create the storage backend based on config.

    When ``STORAGE_BACKEND`` is ``"s3"`` an :class:`S3Storage` instance
    is returned; for any other value (including the validated default
    ``"local"``) a :class:`LocalStorage` instance is returned.
    """
    from sandcastle.config import settings

    if settings.storage_backend == "s3":
        return S3Storage(
            bucket=settings.storage_bucket,
            endpoint_url=settings.storage_endpoint or None,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
    return LocalStorage()
