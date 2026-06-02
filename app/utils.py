from __future__ import annotations

import re
import secrets
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Batch, FileItem
from app.storage import local_batch_folder, local_file_path, storage

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9а-яА-ЯёЁ._()\- ]+")
EICAR_MARKER = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"


class UploadScanError(ValueError):
    pass


def make_code(length: int = 8) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def unique_code(db: Session) -> str:
    for _ in range(20):
        code = make_code()
        if not db.query(Batch).filter(Batch.code == code).first():
            return code
    return secrets.token_urlsafe(10).replace("-", "").replace("_", "")[:12]


def safe_filename(filename: str) -> str:
    filename = Path(filename or "file").name.strip() or "file"
    cleaned = _SAFE_NAME_RE.sub("_", filename)
    return cleaned[:180]


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower().strip()


def validate_filename_allowed(filename: str) -> None:
    ext = extension_of(filename)
    if ext and ext in settings.blocked_extensions:
        raise UploadScanError(f"Файл '{filename}' заблокирован: расширение {ext} запрещено")


def expire_at_for(policy: str) -> datetime | None:
    now = datetime.utcnow()
    mapping = {
        "1_download": now + timedelta(days=14),
        "1_day": now + timedelta(days=1),
        "3_days": now + timedelta(days=3),
        "7_days": now + timedelta(days=7),
        "14_days": now + timedelta(days=14),
    }
    return mapping.get(policy, now + timedelta(days=3))


def expire_policy_label(policy: str) -> str:
    return {
        "1_download": "1 скачивание",
        "1_day": "1 день",
        "3_days": "3 дня",
        "7_days": "7 дней",
        "14_days": "14 дней",
    }.get(policy, "3 дня")


def human_size(num: int | None) -> str:
    num = int(num or 0)
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "Б" else f"{int(value)} {unit}"
        value /= 1024
    return f"{num} Б"


def is_batch_expired(batch: Batch) -> bool:
    if batch.is_deleted:
        return True
    return bool(batch.expires_at and datetime.utcnow() > batch.expires_at)


def batch_folder(code: str) -> Path:
    return local_batch_folder(code)


def file_path(item: FileItem) -> Path:
    return local_file_path(item)


def scan_file(path: Path, original_name: str) -> str:
    if settings.antivirus_mode == "off":
        return "skipped"

    validate_filename_allowed(original_name)

    if settings.antivirus_mode == "basic":
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                if EICAR_MARKER in chunk:
                    raise UploadScanError(f"Файл '{original_name}' похож на тестовый вирус EICAR")
        return "clean"

    return "skipped"


def save_upload_file(upload_file: UploadFile, destination: Path) -> int:
    size = 0
    with destination.open("wb") as out:
        while True:
            chunk = upload_file.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_bytes:
                raise ValueError(f"Файл слишком большой. Лимит: {settings.max_upload_mb} МБ")
            out.write(chunk)
    return size


def delete_batch_files(batch: Batch) -> None:
    storage.delete_batch(batch)
    # Also remove local files that belong to expired links.
    folder = settings.upload_dir / batch.code
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)


def mark_batch_deleted(batch: Batch, *, by_admin: bool = False) -> None:
    batch.is_deleted = True
    batch.deleted_by_admin = bool(by_admin)
    for item in batch.files:
        item.is_deleted = True


def cleanup_expired_batches(db: Session) -> int:
    now = datetime.utcnow()
    batches = db.query(Batch).filter(Batch.is_deleted == False, Batch.expires_at != None, Batch.expires_at < now).all()  # noqa: E712
    count = 0
    for batch in batches:
        delete_batch_files(batch)
        mark_batch_deleted(batch)
        count += 1
    db.commit()
    return count
