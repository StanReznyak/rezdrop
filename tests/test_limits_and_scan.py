import pytest
from app.utils import UploadScanError, validate_filename_allowed


def test_blocked_extension_is_rejected():
    with pytest.raises(UploadScanError):
        validate_filename_allowed("run.bat")


def test_normal_extension_is_allowed():
    validate_filename_allowed("document.pdf")
