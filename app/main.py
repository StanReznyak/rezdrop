from __future__ import annotations

import hmac
import secrets
import tempfile
import threading
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.db import SessionLocal, check_database, get_db, init_db
from app.models import ActivityLog, Batch, CleanupRun, DownloadLog, FileItem, User
from app.security import (
    build_security_headers,
    get_csrf_token,
    hash_password,
    is_strong_enough_password,
    validate_csrf,
    validate_username,
    verify_admin_password,
    verify_password,
)
from app.storage import storage
from app.utils import (
    UploadScanError,
    cleanup_expired_batches,
    delete_batch_files,
    expire_at_for,
    expire_policy_label,
    file_path,
    human_size,
    is_batch_expired,
    mark_batch_deleted,
    safe_filename,
    scan_file,
    unique_code,
    validate_filename_allowed,
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)
if "*" not in settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="strict",
    https_only=settings.cookie_secure,
    max_age=settings.session_max_age_seconds,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["human_size"] = human_size
templates.env.filters["expire_policy_label"] = expire_policy_label

_UPLOAD_EVENTS: dict[str, list[datetime]] = defaultdict(list)
_AUTH_EVENTS: dict[str, list[datetime]] = defaultdict(list)
_CLEANUP_THREAD_STARTED = False

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in build_security_headers(https_enabled=settings.cookie_secure).items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith(("/admin", "/dashboard", "/login", "/register")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    start_cleanup_worker_once()


def start_cleanup_worker_once() -> None:
    global _CLEANUP_THREAD_STARTED
    if _CLEANUP_THREAD_STARTED or not settings.enable_background_cleanup or settings.cleanup_interval_seconds <= 0:
        return
    _CLEANUP_THREAD_STARTED = True

    def worker() -> None:
        while True:
            time.sleep(settings.cleanup_interval_seconds)
            db = SessionLocal()
            try:
                run_cleanup(db, trigger="worker")
            finally:
                db.close()

    threading.Thread(target=worker, daemon=True, name="rezdrop-cleanup-worker").start()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def get_or_create_client_id(request: Request) -> str:
    session_id = request.session.get("client_id")
    if not session_id:
        session_id = secrets.token_urlsafe(24)
        request.session["client_id"] = session_id
    return str(session_id)


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        request.session.pop("user_id", None)
        return None
    user = db.query(User).filter(User.id == user_id_int, User.is_active == True).first()  # noqa: E712
    if not user:
        request.session.pop("user_id", None)
        return None
    return user


def require_user(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Нужен вход в аккаунт")
    return user


def base_context(request: Request, db: Session, **extra):
    ctx = {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "current_user": current_user(request, db),
        "csrf_token": get_csrf_token(request),
    }
    ctx.update(extra)
    return ctx


def _check_rate(events_store: dict[str, list[datetime]], key: str, limit: int, label: str) -> None:
    now = datetime.utcnow()
    hour_ago = now - timedelta(hours=1)
    events = [ts for ts in events_store[key] if ts > hour_ago]
    if len(events) >= limit:
        raise HTTPException(status_code=429, detail=f"Слишком много попыток. Лимит {label}: {limit} в час.")
    events.append(now)
    events_store[key] = events


def check_upload_rate_limit(request: Request) -> None:
    _check_rate(_UPLOAD_EVENTS, client_ip(request), settings.upload_rate_limit_per_hour, "загрузок")


def check_auth_rate_limit(request: Request) -> None:
    _check_rate(_AUTH_EVENTS, client_ip(request), settings.auth_rate_limit_per_hour, "входов/регистраций")


def require_csrf(request: Request, token: str | None) -> None:
    validate_csrf(request, token)


def log_action(
    db: Session,
    *,
    request: Request,
    action: str,
    user: User | None = None,
    batch: Batch | None = None,
    details: str | None = None,
) -> None:
    db.add(
        ActivityLog(
            user_id=user.id if user else None,
            batch_id=batch.id if batch else None,
            action=action[:64],
            ip_address=client_ip(request),
            details=(details or "")[:1000] or None,
        )
    )


def run_cleanup(db: Session, *, trigger: str) -> int:
    deleted = cleanup_expired_batches(db)
    db.add(CleanupRun(deleted_batches=deleted, trigger=trigger[:32]))
    db.commit()
    return deleted


def log_download(db: Session, *, batch: Batch, file_id: int | None, event_type: str, request: Request) -> None:
    user = current_user(request, db)
    db.add(
        DownloadLog(
            batch_id=batch.id,
            file_id=file_id,
            user_id=user.id if user else None,
            event_type=event_type[:32],
            ip_address=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:800],
        )
    )
    log_action(db, request=request, user=user, batch=batch, action=f"download_{event_type}")


def is_admin(request: Request) -> bool:
    return bool(request.session.get("admin_logged_in"))


def ensure_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="Нужен вход в админку")


def create_batch_with_files(
    *,
    request: Request,
    files: list[UploadFile],
    title: str | None,
    expires_in: str,
    password: str | None,
    db: Session,
) -> Batch:
    run_cleanup(db, trigger="upload")
    check_upload_rate_limit(request)

    selected_files = [f for f in files if f.filename]
    if not selected_files:
        raise HTTPException(status_code=400, detail="Файлы не выбраны")
    if len(selected_files) > settings.max_files_per_upload:
        raise HTTPException(status_code=413, detail=f"Слишком много файлов. Лимит: {settings.max_files_per_upload}")

    batch: Batch | None = None

    try:
        for upload_file in selected_files:
            validate_filename_allowed(upload_file.filename or "file")

        user = current_user(request, db)
        code = unique_code(db)
        batch = Batch(
            code=code,
            title=(title or "").strip()[:255] or None,
            password_hash=hash_password(password.strip()) if password and password.strip() else None,
            expires_at=expire_at_for(expires_in),
            expire_policy=expires_in,
            user_id=user.id if user else None,
            session_id=get_or_create_client_id(request),
            ip_address=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:800],
        )
        db.add(batch)
        db.flush()

        total_size = 0
        saved_count = 0

        for upload_file in selected_files:
            original_name = safe_filename(upload_file.filename or "file")
            stored_name = f"{secrets.token_hex(10)}_{original_name}"
            size_bytes, storage_key, scan_path = storage.save_upload(upload_file, code, stored_name)
            total_size += size_bytes

            if total_size > settings.max_total_upload_bytes:
                raise ValueError(f"Общий размер загрузки слишком большой. Лимит: {settings.max_total_upload_mb} МБ")

            if scan_path is None:
                raise UploadScanError(f"Не удалось проверить файл '{original_name}'")

            try:
                scan_status = scan_file(scan_path, original_name)
            finally:
                if settings.storage_backend == "s3":
                    scan_path.unlink(missing_ok=True)

            item = FileItem(
                batch_id=batch.id,
                original_name=original_name,
                stored_name=stored_name,
                storage_backend=settings.storage_backend,
                storage_key=storage_key,
                content_type=upload_file.content_type,
                size_bytes=size_bytes,
                scan_status=scan_status,
            )
            db.add(item)
            saved_count += 1

        if saved_count == 0:
            raise HTTPException(status_code=400, detail="Файлы не выбраны")

        batch.total_size_bytes = total_size
        batch.files_count = saved_count
        log_action(db, request=request, user=user, batch=batch, action="upload", details=f"{saved_count} files, {total_size} bytes")
        db.commit()
        db.refresh(batch)
        return batch
    except HTTPException:
        db.rollback()
        if batch is not None:
            delete_batch_files(batch)
        raise
    except UploadScanError as exc:
        db.rollback()
        if batch is not None:
            delete_batch_files(batch)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        if batch is not None:
            delete_batch_files(batch)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        if batch is not None:
            delete_batch_files(batch)
        raise


def get_batch_or_404(code: str, db: Session) -> Batch:
    batch = db.query(Batch).filter(Batch.code == code).first()
    if not batch or is_batch_expired(batch):
        raise HTTPException(status_code=404, detail="Ссылка не найдена или уже истекла")
    return batch


def has_access(request: Request, batch: Batch) -> bool:
    if not batch.password_hash:
        return True
    return bool(request.session.get(f"batch_access_{batch.code}"))


def active_files(batch: Batch) -> list[FileItem]:
    result: list[FileItem] = []
    for item in batch.files:
        if item.is_deleted:
            continue
        if item.storage_backend == "s3" or storage.exists(item):
            result.append(item)
    return result


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    get_or_create_client_id(request)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=base_context(
            request,
            db,
            max_upload_mb=settings.max_upload_mb,
            max_total_upload_mb=settings.max_total_upload_mb,
            max_files_per_upload=settings.max_files_per_upload,
            antivirus_mode=settings.antivirus_mode,
            db_label=settings.db_label,
            storage_label=settings.storage_label,
        ),
    )


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="register.html", context=base_context(request, db))


@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    email: str | None = Form(None),
    password: str = Form(...),
    csrf_token: str | None = Form(None),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    check_auth_rate_limit(request)
    username = username.strip().lower()
    email_clean = (email or "").strip().lower() or None
    if not validate_username(username):
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context=base_context(request, db, error="Логин: 3–40 символов, латиница/цифры/._-"),
            status_code=400,
        )
    if not is_strong_enough_password(password):
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context=base_context(request, db, error="Пароль минимум 8 символов и не должен быть очевидным"),
            status_code=400,
        )
    exists_query = db.query(User).filter(User.username == username)
    if email_clean:
        exists_query = db.query(User).filter(or_(User.username == username, User.email == email_clean))
    if exists_query.first():
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context=base_context(request, db, error="Такой пользователь или email уже есть"),
            status_code=400,
        )
    user = User(username=username, email=email_clean, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    log_action(db, request=request, user=user, action="register")
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="login.html", context=base_context(request, db))


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str | None = Form(None),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    check_auth_rate_limit(request)
    login_value = username.strip().lower()
    user = db.query(User).filter(or_(User.username == login_value, User.email == login_value)).first()
    if not user or not verify_password(password, user.password_hash):
        log_action(db, request=request, action="login_failed", details=login_value[:80])
        db.commit()
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=base_context(request, db, error="Неверный логин или пароль"),
            status_code=403,
        )
    user.last_login_at = datetime.utcnow()
    log_action(db, request=request, user=user, action="login")
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf_token: str | None = Form(None), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if user:
        log_action(db, request=request, user=user, action="logout")
        db.commit()
    request.session.pop("user_id", None)
    return RedirectResponse(url="/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    batches = (
        db.query(Batch)
        .filter(Batch.user_id == user.id)
        .order_by(Batch.created_at.desc())
        .limit(100)
        .all()
    )
    activities = (
        db.query(ActivityLog)
        .filter(ActivityLog.user_id == user.id)
        .order_by(ActivityLog.created_at.desc())
        .limit(15)
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=base_context(request, db, batches=batches, activities=activities, now=datetime.utcnow()),
    )


@app.post("/dashboard/batches/{code}/delete")
def user_delete_batch(code: str, request: Request, csrf_token: str | None = Form(None), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    user = require_user(request, db)
    batch = db.query(Batch).filter(Batch.code == code, Batch.user_id == user.id).first()
    if batch and not batch.is_deleted:
        delete_batch_files(batch)
        mark_batch_deleted(batch)
        log_action(db, request=request, user=user, batch=batch, action="user_delete_batch")
        db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/upload")
def upload(
    request: Request,
    files: list[UploadFile] = File(...),
    title: str | None = Form(None),
    expires_in: str = Form("3_days"),
    password: str | None = Form(None),
    csrf_token: str | None = Form(None),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    batch = create_batch_with_files(
        request=request,
        files=files,
        title=title,
        expires_in=expires_in,
        password=password,
        db=db,
    )
    return RedirectResponse(url=f"/u/{batch.code}", status_code=303)


@app.post("/api/upload")
def api_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    title: str | None = Form(None),
    expires_in: str = Form("3_days"),
    password: str | None = Form(None),
    db: Session = Depends(get_db),
):
    batch = create_batch_with_files(
        request=request,
        files=files,
        title=title,
        expires_in=expires_in,
        password=password,
        db=db,
    )
    return {
        "code": batch.code,
        "url": f"{settings.public_base_url}/u/{batch.code}",
        "expires_at": batch.expires_at.isoformat() if batch.expires_at else None,
        "files_count": batch.files_count,
        "total_size_bytes": batch.total_size_bytes,
        "storage": settings.storage_label,
    }


@app.get("/api/me")
def api_me(request: Request, db: Session = Depends(get_db)) -> dict[str, str | int | None]:
    user = current_user(request, db)
    return {"authenticated": bool(user), "id": user.id if user else None, "username": user.username if user else None}


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    session_id = get_or_create_client_id(request)
    batches = (
        db.query(Batch)
        .filter(Batch.session_id == session_id)
        .order_by(Batch.created_at.desc())
        .limit(50)
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context=base_context(request, db, batches=batches, now=datetime.utcnow()),
    )


@app.get("/u/{code}", response_class=HTMLResponse)
def public_batch(code: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    batch = get_batch_or_404(code, db)
    locked = bool(batch.password_hash and not has_access(request, batch))
    return templates.TemplateResponse(
        request=request,
        name="batch.html",
        context=base_context(
            request,
            db,
            batch=batch,
            locked=locked,
            public_url=f"{settings.public_base_url}/u/{batch.code}",
            zip_url=f"{settings.public_base_url}/u/{batch.code}/download.zip",
            active_files=active_files(batch),
        ),
    )


@app.post("/u/{code}/unlock")
def unlock_batch(
    code: str,
    request: Request,
    password: str = Form(...),
    csrf_token: str | None = Form(None),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    batch = get_batch_or_404(code, db)
    if not verify_password(password, batch.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="batch.html",
            context=base_context(
                request,
                db,
                batch=batch,
                locked=True,
                public_url=f"{settings.public_base_url}/u/{batch.code}",
                zip_url=f"{settings.public_base_url}/u/{batch.code}/download.zip",
                active_files=[],
                error="Пароль неверный",
            ),
            status_code=403,
        )
    request.session[f"batch_access_{batch.code}"] = True
    log_action(db, request=request, user=current_user(request, db), batch=batch, action="unlock_batch")
    db.commit()
    return RedirectResponse(url=f"/u/{code}", status_code=303)


@app.get("/f/{file_id}")
def download_file(
    file_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    item = db.query(FileItem).filter(FileItem.id == file_id).first()
    if not item or item.is_deleted or is_batch_expired(item.batch):
        raise HTTPException(status_code=404, detail="Файл не найден или уже истек")
    if not has_access(request, item.batch):
        return RedirectResponse(url=f"/u/{item.batch.code}", status_code=303)

    if not storage.exists(item):
        item.is_deleted = True
        db.commit()
        raise HTTPException(status_code=404, detail="Файл уже удалён из хранилища")

    item.download_count += 1
    item.batch.download_count += 1
    log_download(db, batch=item.batch, file_id=item.id, event_type="file", request=request)

    filename = quote(item.original_name)
    if item.storage_backend == "s3":
        tmp_dir = Path(tempfile.gettempdir()) / "rezdrop_s3_downloads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{item.id}_{secrets.token_hex(6)}_{safe_filename(item.original_name)}"
        storage.download_to_path(item, tmp_path)
        response_path = tmp_path
        background_tasks.add_task(Path(tmp_path).unlink, missing_ok=True)
    else:
        response_path = file_path(item)

    response = FileResponse(
        response_path,
        media_type=item.content_type or "application/octet-stream",
        filename=item.original_name,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )

    if item.batch.expire_policy == "1_download":
        mark_batch_deleted(item.batch)
        background_tasks.add_task(delete_batch_files, item.batch)

    db.commit()
    return response


@app.get("/u/{code}/download.zip")
def download_batch_zip(
    code: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    batch = get_batch_or_404(code, db)
    if not has_access(request, batch):
        return RedirectResponse(url=f"/u/{batch.code}", status_code=303)

    files = active_files(batch)
    if not files:
        raise HTTPException(status_code=404, detail="В этой ссылке нет доступных файлов")

    tmp_dir = Path(tempfile.gettempdir()) / "rezdrop_zip"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tmp_dir / f"rezdrop_{batch.code}_{secrets.token_hex(6)}.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        used_names: set[str] = set()
        for item in files:
            src = file_path(item)
            temp_download: Path | None = None
            if item.storage_backend == "s3":
                temp_download = tmp_dir / f"s3_{item.id}_{secrets.token_hex(6)}"
                storage.download_to_path(item, temp_download)
                src = temp_download
            arcname = Path(item.original_name).name
            if arcname in used_names:
                stem = Path(arcname).stem
                suffix = Path(arcname).suffix
                arcname = f"{stem}_{item.id}{suffix}"
            used_names.add(arcname)
            archive.write(src, arcname=arcname)
            if temp_download:
                temp_download.unlink(missing_ok=True)
            item.download_count += 1

    batch.download_count += 1
    log_download(db, batch=batch, file_id=None, event_type="zip", request=request)
    if batch.expire_policy == "1_download":
        mark_batch_deleted(batch)
        background_tasks.add_task(delete_batch_files, batch)

    db.commit()
    background_tasks.add_task(Path(zip_path).unlink, missing_ok=True)
    return FileResponse(zip_path, media_type="application/zip", filename=f"RezDrop_{batch.code}.zip")


@app.post("/cleanup")
def cleanup(request: Request, csrf_token: str | None = Form(None), db: Session = Depends(get_db)) -> JSONResponse:
    ensure_admin(request)
    require_csrf(request, csrf_token)
    deleted = run_cleanup(db, trigger="api")
    log_action(db, request=request, action="cleanup", details=f"deleted={deleted}")
    db.commit()
    return JSONResponse({"deleted_batches": deleted})


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="admin_login.html", context=base_context(request, db))


@app.post("/admin/login")
def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str | None = Form(None),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    check_auth_rate_limit(request)
    ok_user = hmac.compare_digest(username, settings.admin_username)
    ok_pass = verify_admin_password(password, plain_password=settings.admin_password, password_hash=settings.admin_password_hash)
    if not (ok_user and ok_pass):
        log_action(db, request=request, action="admin_login_failed", details=username[:80])
        db.commit()
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context=base_context(request, db, error="Неверный логин или пароль"),
            status_code=403,
        )
    request.session["admin_logged_in"] = True
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/logout")
def admin_logout(request: Request, csrf_token: str | None = Form(None)):
    require_csrf(request, csrf_token)
    request.session.pop("admin_logged_in", None)
    return RedirectResponse(url="/", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    auto_deleted = run_cleanup(db, trigger="admin_open")
    recent_batches = db.query(Batch).order_by(Batch.created_at.desc()).limit(120).all()
    recent_downloads = db.query(DownloadLog).order_by(DownloadLog.created_at.desc()).limit(10).all()
    recent_cleanups = db.query(CleanupRun).order_by(CleanupRun.created_at.desc()).limit(5).all()
    recent_actions = db.query(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(15).all()
    all_batches = db.query(Batch).all()
    files = db.query(FileItem).all()
    users = db.query(User).all()
    now = datetime.utcnow()
    active_batches = [b for b in all_batches if not b.is_deleted and not is_batch_expired(b)]
    expired_batches = [b for b in all_batches if not b.is_deleted and b.expires_at and b.expires_at < now]

    cleanup_deleted_raw = request.query_params.get("cleanup_deleted")
    cleanup_message = None
    if cleanup_deleted_raw is not None:
        try:
            cleanup_deleted = int(cleanup_deleted_raw)
        except ValueError:
            cleanup_deleted = 0
        if cleanup_deleted > 0:
            cleanup_message = f"Очистка готова: удалено просроченных ссылок — {cleanup_deleted}."
        else:
            cleanup_message = "Очистка готова: просроченных ссылок нет. Активные ссылки она не трогает."
    elif auto_deleted > 0:
        cleanup_message = f"Автоочистка при открытии админки удалила просроченных ссылок — {auto_deleted}."

    stats = {
        "total_batches": len(all_batches),
        "active_batches": len(active_batches),
        "expired_batches": len(expired_batches),
        "total_files": len(files),
        "total_size": sum((f.size_bytes or 0) for f in files if not f.is_deleted),
        "downloads": sum((b.download_count or 0) for b in all_batches),
        "users": len(users),
        "actions": db.query(ActivityLog).count(),
    }
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=base_context(
            request,
            db,
            batches=recent_batches,
            stats=stats,
            now=now,
            cleanup_message=cleanup_message,
            recent_downloads=recent_downloads,
            recent_cleanups=recent_cleanups,
            recent_actions=recent_actions,
            db_label=settings.db_label,
            storage_label=settings.storage_label,
            app_env=settings.app_env,
        ),
    )


@app.post("/admin/batches/{code}/delete")
def admin_delete_batch(code: str, request: Request, csrf_token: str | None = Form(None), db: Session = Depends(get_db)):
    ensure_admin(request)
    require_csrf(request, csrf_token)
    batch = db.query(Batch).filter(Batch.code == code).first()
    if batch:
        delete_batch_files(batch)
        mark_batch_deleted(batch, by_admin=True)
        log_action(db, request=request, user=current_user(request, db), batch=batch, action="admin_delete_batch")
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/cleanup")
def admin_cleanup(request: Request, csrf_token: str | None = Form(None), db: Session = Depends(get_db)):
    ensure_admin(request)
    require_csrf(request, csrf_token)
    deleted = run_cleanup(db, trigger="admin_button")
    log_action(db, request=request, action="admin_cleanup", details=f"deleted={deleted}")
    db.commit()
    return RedirectResponse(url=f"/admin?cleanup_deleted={deleted}", status_code=303)


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> HTMLResponse:
    return HTMLResponse("", media_type="application/javascript")


@app.get("/health")
def health() -> dict[str, str]:
    db_ok = check_database()
    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "database": settings.db_label,
        "database_ok": str(db_ok).lower(),
        "storage": settings.storage_label,
    }


@app.get("/api/status")
def api_status() -> dict[str, str | int]:
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "env": settings.app_env,
        "database": settings.db_label,
        "storage": settings.storage_label,
        "max_upload_mb": settings.max_upload_mb,
        "max_total_upload_mb": settings.max_total_upload_mb,
        "max_files_per_upload": settings.max_files_per_upload,
        "upload_rate_limit_per_hour": settings.upload_rate_limit_per_hour,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=True)
