"""Blob storage for MUR Drive: local disk (persistent volume) or S3-compatible object storage.

Metadata (names, folders, sizes) lives in the database; this layer only
stores bytes under opaque keys ``<user_id>/<item_id>``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import AsyncIterator, BinaryIO


class Storage:
    async def put_file(self, key: str, src: Path) -> None:
        raise NotImplementedError

    async def open_stream(self, key: str, chunk: int = 256 * 1024) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def read_bytes(self, key: str, limit: int | None = None) -> bytes:
        out = bytearray()
        async for part in self.open_stream(key):
            out += part
            if limit is not None and len(out) >= limit:
                return bytes(out[:limit])
        return bytes(out)

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def copy_to_path(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            async for part in self.open_stream(key):
                fh.write(part)


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    async def put_file(self, key: str, src: Path) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, src, dest)

    async def open_stream(self, key: str, chunk: int = 256 * 1024) -> AsyncIterator[bytes]:
        path = self._path(key)
        fh: BinaryIO = await asyncio.to_thread(open, path, "rb")
        try:
            while True:
                data = await asyncio.to_thread(fh.read, chunk)
                if not data:
                    break
                yield data
        finally:
            fh.close()

    async def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            await asyncio.to_thread(os.remove, p)


class S3Storage(Storage):
    """Any S3-compatible service (AWS S3, Cloudflare R2, Backblaze B2, MinIO)."""

    def __init__(self, bucket: str, endpoint_url: str | None = None, region: str | None = None, prefix: str = "drive/"):
        import boto3  # optional dependency

        if not bucket:
            raise RuntimeError("MUR_S3_BUCKET is required when MUR_STORAGE=s3")
        self.bucket = bucket
        self.prefix = prefix
        # Credentials come from the standard AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables.
        self.client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def _key(self, key: str) -> str:
        return self.prefix + key

    async def put_file(self, key: str, src: Path) -> None:
        await asyncio.to_thread(self.client.upload_file, str(src), self.bucket, self._key(key))

    async def open_stream(self, key: str, chunk: int = 256 * 1024) -> AsyncIterator[bytes]:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=self._key(key))
        body = obj["Body"]
        try:
            while True:
                data = await asyncio.to_thread(body.read, chunk)
                if not data:
                    break
                yield data
        finally:
            body.close()

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=self._key(key))


def make_storage(settings) -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage(settings.s3_bucket, settings.s3_endpoint, settings.s3_region)
    return LocalStorage(settings.data_dir / "blobs")
