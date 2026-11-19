from pathlib import Path

from pc_manager_server.config import _load_or_create_admin_password


def test_generated_admin_password_is_exactly_eight_characters(tmp_path: Path):
    password_file = tmp_path / "admin_password.txt"

    password = _load_or_create_admin_password(password_file)

    assert len(password) == 8
    assert password_file.read_text().strip() == password
    assert password_file.stat().st_mode & 0o777 == 0o600


def test_legacy_generated_admin_password_is_rotated_to_eight_characters(tmp_path: Path):
    password_file = tmp_path / "admin_password.txt"
    password_file.write_text("legacy-generated-password-longer-than-eight\n")

    password = _load_or_create_admin_password(password_file)

    assert len(password) == 8
    assert password != "legacy-generated-password-longer-than-eight"
    assert password_file.read_text().strip() == password
    assert password_file.stat().st_mode & 0o777 == 0o600


def test_existing_eight_character_admin_password_is_preserved(tmp_path: Path):
    password_file = tmp_path / "admin_password.txt"
    password_file.write_text("aB3_dE9-\n")

    password = _load_or_create_admin_password(password_file)

    assert password == "aB3_dE9-"
