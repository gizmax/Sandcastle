"""Tests for LocalStorage backend."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from sandcastle.engine.storage import LocalStorage


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(base_dir=str(tmp_path))


class TestLocalStorage:

    @pytest.mark.asyncio
    async def test_write_and_read(self, storage):
        await storage.write("test.txt", "hello world")
        content = await storage.read("test.txt")
        assert content == "hello world"

    @pytest.mark.asyncio
    async def test_read_nonexistent_returns_none(self, storage):
        result = await storage.read("does-not-exist.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_write_creates_subdirectories(self, storage):
        await storage.write("sub/dir/file.json", '{"key": "value"}')
        content = await storage.read("sub/dir/file.json")
        assert content == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_overwrite(self, storage):
        await storage.write("test.txt", "v1")
        await storage.write("test.txt", "v2")
        content = await storage.read("test.txt")
        assert content == "v2"

    @pytest.mark.asyncio
    async def test_delete(self, storage):
        await storage.write("test.txt", "content")
        await storage.delete("test.txt")
        result = await storage.read("test.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, storage):
        await storage.delete("does-not-exist.txt")

    @pytest.mark.asyncio
    async def test_list_files(self, storage):
        await storage.write("data/a.txt", "a")
        await storage.write("data/b.txt", "b")
        await storage.write("other/c.txt", "c")
        files = await storage.list("data/")
        assert "data/a.txt" in files
        assert "data/b.txt" in files
        assert "other/c.txt" not in files

    @pytest.mark.asyncio
    async def test_list_empty_prefix(self, storage):
        result = await storage.list("nonexistent/")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_returns_sorted(self, storage):
        await storage.write("data/c.txt", "c")
        await storage.write("data/a.txt", "a")
        await storage.write("data/b.txt", "b")
        files = await storage.list("data/")
        assert files == sorted(files)

    def test_path_traversal_denied(self, storage):
        with pytest.raises(ValueError, match="Path traversal denied"):
            storage._safe_path("../../etc/passwd")

    def test_path_traversal_dot_dot(self, storage):
        with pytest.raises(ValueError, match="Path traversal denied"):
            storage._safe_path("../outside")

    def test_safe_path_normal(self, storage):
        path = storage._safe_path("data/file.json")
        assert path.is_relative_to(storage.base_dir)

    @pytest.mark.asyncio
    async def test_unicode_content(self, storage):
        await storage.write("unicode.txt", "Ahoj svete! Prilis zlutoucky kun.")
        content = await storage.read("unicode.txt")
        assert content == "Ahoj svete! Prilis zlutoucky kun."
