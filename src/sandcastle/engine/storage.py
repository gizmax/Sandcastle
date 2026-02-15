"""Storage backends for persistent data between workflow runs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class StorageBackend(Protocol):
    """Protocol for storage backend implementations."""

    async def read(self, path: str) -> str | None: ...
    async def write(self, path: str, content: str) -> None: ...
    async def list(self, prefix: str) -> list[str]: ...
    async def delete(self, path: str) -> None: ...


class LocalStorage:
    """Filesystem-based storage backend for local development."""

    def __init__(self, base_dir: str = "./data/storage") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def read(self, path: str) -> str | None:
        """Read content from a file."""
        file_path = self.base_dir / path
        if not file_path.exists():
            return None
        return file_path.read_text(encoding="utf-8")

    async def write(self, path: str, content: str) -> None:
        """Write content to a file."""
        file_path = self.base_dir / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    async def list(self, prefix: str) -> list[str]:
        """List files matching a prefix."""
        prefix_path = self.base_dir / prefix
        if not prefix_path.parent.exists():
            return []
        results: list[str] = []
        search_dir = prefix_path.parent if not prefix_path.is_dir() else prefix_path
        for p in search_dir.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(self.base_dir))
                if rel.startswith(prefix):
                    results.append(rel)
        return sorted(results)

    async def delete(self, path: str) -> None:
        """Delete a file."""
        file_path = self.base_dir / path
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
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key

    def _get_session(self):
        """Create an aioboto3 session."""
        import aioboto3

        return aioboto3.Session(
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
        )

    async def read(self, path: str) -> str | None:
        """Read content from S3."""
        session = self._get_session()
        async with session.client("s3", endpoint_url=self.endpoint_url) as s3:
            try:
                resp = await s3.get_object(Bucket=self.bucket, Key=path)
                body = await resp["Body"].read()
                return body.decode("utf-8")
            except s3.exceptions.NoSuchKey:
                return None
            except Exception as e:
                logger.error(f"S3 read error for '{path}': {e}")
                return None

    async def write(self, path: str, content: str) -> None:
        """Write content to S3."""
        session = self._get_session()
        async with session.client("s3", endpoint_url=self.endpoint_url) as s3:
            await s3.put_object(
                Bucket=self.bucket,
                Key=path,
                Body=content.encode("utf-8"),
                ContentType="application/json",
            )

    async def list(self, prefix: str) -> list[str]:
        """List objects matching a prefix."""
        session = self._get_session()
        async with session.client("s3", endpoint_url=self.endpoint_url) as s3:
            results: list[str] = []
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    results.append(obj["Key"])
            return results

    async def delete(self, path: str) -> None:
        """Delete an object from S3."""
        session = self._get_session()
        async with session.client("s3", endpoint_url=self.endpoint_url) as s3:
            await s3.delete_object(Bucket=self.bucket, Key=path)
