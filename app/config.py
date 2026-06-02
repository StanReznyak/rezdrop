from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


class Settings:
    app_name: str = os.getenv("APP_NAME", "RezDrop")
    app_version: str = os.getenv("APP_VERSION", "0.4.16")
    app_env: str = os.getenv("APP_ENV", "local")
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = _int_env("APP_PORT", 8080)
    secret_key: str = os.getenv("APP_SECRET_KEY", "dev-change-me")
    cookie_secure: bool = _bool_env("COOKIE_SECURE", False)
    session_max_age_seconds: int = _int_env("SESSION_MAX_AGE_SECONDS", 60 * 60 * 24 * 7)
    allowed_hosts_raw: str = os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost")
    enable_api_docs: bool = _bool_env("ENABLE_API_DOCS", True)

    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./data/rezdrop.db",
    )
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "uploads"))
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8080").rstrip("/")

    max_upload_mb: int = _int_env("MAX_UPLOAD_MB", 512)
    max_total_upload_mb: int = _int_env("MAX_TOTAL_UPLOAD_MB", 2048)
    max_files_per_upload: int = _int_env("MAX_FILES_PER_UPLOAD", 50)

    # Request limits. Runtime counters are stored in memory; audit logs remain persistent.
    upload_rate_limit_per_hour: int = _int_env("UPLOAD_RATE_LIMIT_PER_HOUR", 30)
    auth_rate_limit_per_hour: int = _int_env("AUTH_RATE_LIMIT_PER_HOUR", 40)
    auth_fail_rate_limit_per_15_min: int = _int_env("AUTH_FAIL_RATE_LIMIT_PER_15_MIN", 10)


    # Simple file check: blocks dangerous extensions and the EICAR test signature.
    antivirus_mode: str = os.getenv("ANTIVIRUS_MODE", "basic").lower().strip()
    blocked_extensions: set[str] = {
        ext.strip().lower()
        for ext in os.getenv(
            "BLOCKED_EXTENSIONS",
            ".bat,.cmd,.com,.scr,.vbs,.js,.jse,.wsf,.ps1,.msi,.jar,.exe",
        ).split(",")
        if ext.strip()
    }

    cleanup_interval_seconds: int = _int_env("CLEANUP_INTERVAL_SECONDS", 600)
    enable_background_cleanup: bool = _bool_env("ENABLE_BACKGROUND_CLEANUP", True)

    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "change-me-local-password")
    admin_password_hash: str | None = os.getenv("ADMIN_PASSWORD_HASH") or None

    # Storage: local by default, S3/MinIO for Docker/VPS.
    # STORAGE_BACKEND=local|s3
    storage_backend: str = os.getenv("STORAGE_BACKEND", "local").strip().lower()
    s3_endpoint_url: str | None = os.getenv("S3_ENDPOINT_URL") or None
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "rezdrop")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "change_me_minio_password")
    s3_bucket_name: str = os.getenv("S3_BUCKET_NAME", "rezdrop")
    s3_region: str = os.getenv("S3_REGION", "us-east-1")
    s3_presigned_ttl_seconds: int = _int_env("S3_PRESIGNED_TTL_SECONDS", 3600)
    s3_auto_create_bucket: bool = _bool_env("S3_AUTO_CREATE_BUCKET", True)

    @property
    def allowed_hosts(self) -> list[str]:
        hosts = [host.strip() for host in self.allowed_hosts_raw.split(",") if host.strip()]
        return hosts or ["127.0.0.1", "localhost"]

    def validate_runtime_security(self) -> None:
        if self.app_env.lower() not in {"production", "prod"}:
            return
        weak_secret_values = {"dev-change-me", "dev-local-change-me", "CHANGE_ME_LONG_RANDOM_SECRET", "test-app-secret-key-123456"}
        if self.secret_key in weak_secret_values or len(self.secret_key) < 32:
            raise RuntimeError("Production mode requires a strong APP_SECRET_KEY with at least 32 characters")
        if not self.admin_password_hash and self.admin_password in {"change_me_admin_password", "password", "password123", "qwerty123"}:
            raise RuntimeError("Production mode requires a strong ADMIN_PASSWORD or ADMIN_PASSWORD_HASH")
        if self.storage_backend == "s3" and self.s3_secret_key in {"rezdrop_password", "change_me_minio_password", "password"}:
            raise RuntimeError("Production mode requires a strong S3_SECRET_KEY")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def db_label(self) -> str:
        return "SQLite" if self.is_sqlite else "PostgreSQL"

    @property
    def storage_label(self) -> str:
        return "S3/MinIO" if self.storage_backend == "s3" else "Local FS"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_total_upload_bytes(self) -> int:
        return self.max_total_upload_mb * 1024 * 1024


settings = Settings()
settings.validate_runtime_security()
settings.upload_dir.mkdir(parents=True, exist_ok=True)
