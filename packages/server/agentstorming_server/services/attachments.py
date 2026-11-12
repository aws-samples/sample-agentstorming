# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Attachment storage backends — local disk and S3."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import aiofiles


@dataclass
class AttachmentInfo:
    att_id: UUID
    s3_key: str | None
    local_path: str | None
    content_type: str
    size_bytes: int
    filename: str
    sha256_hex: str


#: Default lifetime of a presigned GET, and the ceiling S3 itself imposes.
#: Short on purpose: the server checks membership when it presigns, and a
#: presigned URL keeps working for its whole lifetime whatever happens to the
#: member who holds it. It used to be seven days and was copied into every
#: attachment event, so an ejected member kept downloading for a week.
DEFAULT_PRESIGN_TTL_SECONDS = 300
MAX_PRESIGN_TTL_SECONDS = 604_800


class AttachmentStorage:
    async def put(self, *, room_id: str, sender_pid: str, filename: str, data: bytes,
                  content_type: str) -> AttachmentInfo:
        raise NotImplementedError

    def presign_get(self, info: AttachmentInfo) -> str:
        raise NotImplementedError


class LocalAttachmentStorage(AttachmentStorage):
    def __init__(self, base_dir: Path, public_url_base: str) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.public_url_base = public_url_base.rstrip("/")

    async def put(self, *, room_id: str, sender_pid: str, filename: str, data: bytes,
                  content_type: str) -> AttachmentInfo:
        att_id = uuid4()
        room_dir = self.base_dir / room_id
        room_dir.mkdir(parents=True, exist_ok=True)
        key = f"{att_id}-{filename}"
        p = room_dir / key
        async with aiofiles.open(p, "wb") as f:
            await f.write(data)
        sha = hashlib.sha256(data).hexdigest()
        return AttachmentInfo(
            att_id=att_id,
            s3_key=None,
            local_path=str(p),
            content_type=content_type,
            size_bytes=len(data),
            filename=filename,
            sha256_hex=sha,
        )

    def presign_get(self, info: AttachmentInfo) -> str:
        # Local mode: simply serve by path via the /v1/attachments/{id} GET endpoint.
        return f"{self.public_url_base}/v1/attachments/{info.att_id}"


class S3AttachmentStorage(AttachmentStorage):
    def __init__(self, bucket: str, region: str, *,
                 presign_ttl_seconds: int = DEFAULT_PRESIGN_TTL_SECONDS) -> None:
        import boto3
        from botocore.client import Config as BotoConfig
        if not 1 <= presign_ttl_seconds <= MAX_PRESIGN_TTL_SECONDS:
            raise ValueError(
                f"presign_ttl_seconds must be 1 to {MAX_PRESIGN_TTL_SECONDS}, got {presign_ttl_seconds}"
            )
        self.bucket = bucket
        self.region = region
        self.presign_ttl_seconds = presign_ttl_seconds
        self.s3 = boto3.client(
            "s3",
            region_name=region,
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
        )

    async def put(self, *, room_id: str, sender_pid: str, filename: str, data: bytes,
                  content_type: str) -> AttachmentInfo:
        att_id = uuid4()
        key = f"{room_id}/{att_id}-{filename}"
        import asyncio
        await asyncio.to_thread(
            self.s3.put_object,
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
            ServerSideEncryption="AES256",
        )
        sha = hashlib.sha256(data).hexdigest()
        return AttachmentInfo(
            att_id=att_id,
            s3_key=key,
            local_path=None,
            content_type=content_type,
            size_bytes=len(data),
            filename=filename,
            sha256_hex=sha,
        )

    def presign_get(self, info: AttachmentInfo) -> str:
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": info.s3_key},
            ExpiresIn=self.presign_ttl_seconds,
        )
