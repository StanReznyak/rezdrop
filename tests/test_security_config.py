import os
import subprocess
import sys
from pathlib import Path


def test_production_rejects_default_admin_password():
    code = "from app.config import Settings; Settings().validate_runtime_security()"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "production",
            "APP_SECRET_KEY": "x" * 32,
            "ADMIN_PASSWORD": "change-me-local-password",
            "STORAGE_BACKEND": "local",
        }
    )
    result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "ADMIN_PASSWORD" in result.stderr


def test_register_password_minlength_matches_backend_policy():
    template = Path("app/templates/register.html").read_text()
    assert 'name="password"' in template
    assert 'minlength="8"' in template
    assert 'minlength="6"' not in template
