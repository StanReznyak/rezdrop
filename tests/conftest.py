import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_rezdrop.db")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("APP_SECRET_KEY", "test-app-secret-key-123456")
os.environ.setdefault("ADMIN_PASSWORD", "change-me-local-password")
os.environ.setdefault("ALLOWED_HOSTS", "testserver,127.0.0.1,localhost")
os.environ.setdefault("ENABLE_API_DOCS", "true")
