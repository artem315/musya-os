import os

import pytest

moto = pytest.importorskip("moto")


async def test_s3_storage_roundtrip(tmp_path):
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
    with moto.mock_aws():
        import boto3
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="murdrive")
        from app.storage import S3Storage
        st = S3Storage("murdrive", region="us-east-1")
        src = tmp_path / "a.txt"
        src.write_bytes(b"x" * 300_000)
        await st.put_file("u1/item1", src)
        assert await st.read_bytes("u1/item1") == b"x" * 300_000
        await st.copy_to_path("u1/item1", tmp_path / "out" / "b.txt")
        assert (tmp_path / "out" / "b.txt").stat().st_size == 300_000
        await st.delete("u1/item1")
        with pytest.raises(Exception):
            await st.read_bytes("u1/item1")


async def test_local_storage_rejects_escape(tmp_path):
    from app.storage import LocalStorage
    st = LocalStorage(tmp_path / "blobs")
    with pytest.raises(ValueError):
        await st.put_file("../../etc/x", tmp_path)
