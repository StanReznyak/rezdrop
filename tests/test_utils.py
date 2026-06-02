from app.utils import expire_policy_label, human_size, safe_filename


def test_human_size():
    assert human_size(1024) == "1.0 КБ"


def test_safe_filename_removes_path():
    assert ".." not in safe_filename("../../secret.txt")
    assert safe_filename("../../secret.txt").endswith("secret.txt")


def test_expire_policy_label():
    assert expire_policy_label("3_days") == "3 дня"
