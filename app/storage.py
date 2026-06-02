from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile

from app.config import settings
from app.models import Batch, FileItem

_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def _safe_under_uploads(*parts: str) -> Path:
    base = settings.upload_dir.resolve()
    candidate = base.joinpath(*parts).resolve()
    if base != candidate and base not in candidate.parents:
        raise StorageError("Unsafe storage path")
    return candidate


def _safe_batch_code(code: str) -> str:
    if not _CODE_RE.fullmatch(code or ""):
        raise StorageError("Unsafe batch code")
    return code


def local_batch_folder(code: str) -> Path:
    folder = _safe_under_uploads(_safe_batch_code(code))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def local_file_path(item: FileItem) -> Path:
    return _safe_under_uploads(_safe_batch_code(item.batch.code), Path(item.stored_name).name)


class StorageError(RuntimeError):
    pass


class Storage:
    backend_name = "base"

    def save_upload(self, upload_file: UploadFile, batch_code: str, stored_name: str) -> tuple[int, str, Path | None]:
        raise NotImplementedError

    def open_for_read(self, item: FileItem) -> BinaryIO:
        raise NotImplementedError

    def download_to_path(self, item: FileItem, destination: Path) -> None:
        raise NotImplementedError

    def public_download_url(self, item: FileItem) -> str | None:
        return None

    def exists(self, item: FileItem) -> bool:
        raise NotImplementedError

    def delete_file(self, item: FileItem) -> None:
        raise NotImplementedError

    def delete_batch(self, batch: Batch) -> None:
        for item in batch.files:
            self.delete_file(item)


class LocalStorage(Storage):
    backend_name = "local"

    def save_upload(self, upload_file: UploadFile, batch_code: str, stored_name: str) -> tuple[int, str, Path | None]:
        folder = local_batch_folder(batch_code)
        path = folder / stored_name
        size = 0
        with path.open("wb") as out:
            while True:
                chunk = upload_file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    path.unlink(missing_ok=True)
                    raise ValueError(f"Файл слишком большой. Лимит: {settings.max_upload_mb} МБ")
                out.write(chunk)
        return size, str(path.relative_to(settings.upload_dir.resolve())), path

    def open_for_read(self, item: FileItem) -> BinaryIO:
        return local_file_path(item).open("rb")

    def download_to_path(self, item: FileItem, destination: Path) -> None:
        src = local_file_path(item)
        shutil.copyfile(src, destination)

    def exists(self, item: FileItem) -> bool:
        return local_file_path(item).exists()

    def delete_file(self, item: FileItem) -> None:
        local_file_path(item).unlink(missing_ok=True)

    def delete_batch(self, batch: Batch) -> None:
        folder = settings.upload_dir / batch.code
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)


class S3Storage(Storage):
    backend_name = "s3"

    def __init__(self) -> None:
        try:
            import boto3
            from botocore.exceptions import ClientError
        except Exception as exc:  # pragma: no cover - only happens if dependency missing
            raise StorageError("Для STORAGE_BACKEND=s3 нужен пакет boto3") from exc
        self._client_error = ClientError
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        self.bucket = settings.s3_bucket_name
        self._bucket_checked = False

    def ensure_bucket(self) -> None:
        if self._bucket_checked or not settings.s3_auto_create_bucket:
            return
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)
        self._bucket_checked = True

    def _key(self, batch_code: str, stored_name: str) -> str:
        return f"uploads/{batch_code}/{stored_name}"

    def save_upload(self, upload_file: UploadFile, batch_code: str, stored_name: str) -> tuple[int, str, Path | None]:
        key = self._key(batch_code, stored_name)
        tmp_path = Path(tempfile.gettempdir()) / f"rezdrop_s3_upload_{batch_code}_{stored_name}"
        size = 0
        try:
            with tmp_path.open("wb") as out:
                while True:
                    chunk = upload_file.file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise ValueError(f"Файл слишком большой. Лимит: {settings.max_upload_mb} МБ")
                    out.write(chunk)
            extra_args = {}
            if upload_file.content_type:
                extra_args["ContentType"] = upload_file.content_type
            
            self.ensure_bucket()
            if extra_args:
                self.client.upload_file(str(tmp_path), self.bucket, key, ExtraArgs=extra_args)
            else:
                self.client.upload_file(str(tmp_path), self.bucket, key)
            return size, key, tmp_path
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def open_for_read(self, item: FileItem) -> BinaryIO:
        tmp = Path(tempfile.gettempdir()) / f"rezdrop_s3_read_{item.id}_{Path(item.original_name).name}"
        self.download_to_path(item, tmp)
        return tmp.open("rb")

    def download_to_path(self, item: FileItem, destination: Path) -> None:
        key = item.storage_key or self._key(item.batch.code, item.stored_name)
        self.ensure_bucket()
        self.client.download_file(self.bucket, key, str(destination))

    def public_download_url(self, item: FileItem) -> str | None:
        key = item.storage_key or self._key(item.batch.code, item.stored_name)
        self.ensure_bucket()
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key, "ResponseContentDisposition": f'attachment; filename="{item.original_name}"'},
            ExpiresIn=settings.s3_presigned_ttl_seconds,
        )

    def exists(self, item: FileItem) -> bool:
        key = item.storage_key or self._key(item.batch.code, item.stored_name)
        try:
            self.ensure_bucket()
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def delete_file(self, item: FileItem) -> None:
        key = item.storage_key or self._key(item.batch.code, item.stored_name)
        try:
            self.ensure_bucket()
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            pass


def get_storage() -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage()
    return LocalStorage()


storage = get_storage()
