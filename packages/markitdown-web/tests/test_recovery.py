"""Offline recovery regression: temporary fake accounts/documents, no real data."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

import pytest
from filelock import FileLock

from markitdown_web.auth import AuthError, AuthService
from markitdown_web.jobs import JobService
from markitdown_web.recovery import (
    RecoveryError,
    create_backup,
    export_recovery_journal,
    restore_backup,
)
from markitdown_web.state import Database, Settings

PASSWORD = "Synthetic-recovery-123!"


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(data_dir=tmp_path / "live", start_workers=False)
    database = Database(settings)
    auth = AuthService(database, settings)
    admin = auth.bootstrap_admin("fake-owner", PASSWORD)
    invitation = auth.create_invite(admin["id"])
    user = auth.register("fake-user", PASSWORD, invitation["token"], "127.0.0.1")
    jobs = JobService(database, settings)
    auth.create_invite(admin["id"])
    auth.login(admin["username"], PASSWORD, "127.0.0.1")
    auth.login(user["username"], PASSWORD, "127.0.0.1")
    yield settings, database, auth, jobs, admin, user
    jobs.stop()


def add_job(workspace, user=None, succeeded=True):
    _, db, _, jobs, _, default_user = workspace
    user = user or default_user
    job = jobs.enqueue(
        user["id"],
        [{"filename": "合成.txt", "suffix": ".txt", "data": b"synthetic backup"}],
    )[0]
    if succeeded:
        path = jobs.jobs_dir / job["id"]
        jobs._write(path / "markdown.md", b"# synthetic backup")
        jobs._write(path / "preview.html", b"<h1>synthetic backup</h1>")
        with db.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET status='succeeded',finished_at=? WHERE id=?",
                (time.time(), job["id"]),
            )
            connection.execute(
                "UPDATE job_attempts SET state='succeeded',finished_at=? WHERE job_id=?",
                (time.time(), job["id"]),
            )
    return job


def bundle(workspace, tmp_path):
    backup = tmp_path / "snapshot"
    journal = tmp_path / "current-journal.json"
    create_backup(workspace[0].data_dir, backup)
    export_recovery_journal(workspace[0].data_dir, journal)
    return backup, journal


def restore(backup, journal, target):
    return restore_backup(backup, target, journal, confirm_latest_journal=True)


def connect(root):
    connection = sqlite3.connect(root / "workspace.sqlite3")
    connection.row_factory = sqlite3.Row
    return connection


def rewrite_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def rewrite_journal(path, mutate):
    envelope = json.loads(path.read_text())
    mutate(envelope["journal"])
    envelope["sha256"] = hashlib.sha256(
        json.dumps(
            envelope["journal"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    rewrite_json(path, envelope)


def test_backup_restore_roundtrip_and_private_permissions(workspace, tmp_path):
    job = add_job(workspace)
    backup, journal = bundle(workspace, tmp_path)
    target = tmp_path / "restored"
    result = restore(backup, journal, target)
    assert result["sessions_invalidated"] == 2
    assert result["invites_invalidated"] == 1
    assert (
        target / "jobs" / job["id"] / "markdown.md"
    ).read_bytes() == b"# synthetic backup"
    assert "password_hash" not in journal.read_text()
    assert "token_hash" not in journal.read_text()
    assert PASSWORD not in journal.read_text()
    for path in [backup, target, *backup.rglob("*"), *target.rglob("*"), journal]:
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    with connect(target) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM invites WHERE used_at IS NULL AND revoked_at IS NULL"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_t0_backup_t1_delete_disable_and_quota_t2_restore(workspace, tmp_path):
    settings, db, auth, jobs, admin, user = workspace
    deleted = add_job(workspace, admin)
    disabled = add_job(workspace, user)
    backup = tmp_path / "snapshot"
    create_backup(settings.data_dir, backup)
    jobs.delete_job(admin["id"], deleted["id"])
    add_job(workspace, admin)
    auth.update_user(admin["id"], user["id"], is_active=False)
    journal = tmp_path / "current-journal.json"
    export_recovery_journal(settings.data_dir, journal)
    target = tmp_path / "restored"
    result = restore(backup, journal, target)
    assert result["jobs_filtered"] == 1
    assert not (target / "jobs" / deleted["id"]).exists()
    assert not (target / "jobs" / disabled["id"]).exists()
    with connect(target) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM jobs WHERE id=?", (deleted["id"],)
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT 1 FROM job_attempts WHERE job_id=?", (deleted["id"],)
            ).fetchone()
            is None
        )
        assert connection.execute(
            "SELECT 1 FROM job_tombstones WHERE job_id=?", (deleted["id"],)
        ).fetchone()
        assert (
            connection.execute(
                "SELECT is_active FROM users WHERE id=?", (user["id"],)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT is_active FROM account_revocations WHERE user_id=?",
                (user["id"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT used FROM daily_usage WHERE user_id=?", (admin["id"],)
            ).fetchone()[0]
            == 2
        )
    # The restored ledger carries deletion forward through another older restore.
    newer = tmp_path / "restored-journal.json"
    export_recovery_journal(target, newer)
    target_again = tmp_path / "restored-again"
    restore(backup, newer, target_again)
    assert not (target_again / "jobs" / deleted["id"]).exists()


def test_password_reset_after_snapshot_invalidates_old_hash_without_reactivation(
    workspace, tmp_path
):
    settings, _, auth, _, admin, user = workspace
    backup = tmp_path / "snapshot"
    create_backup(settings.data_dir, backup)
    auth.reset_password(admin["username"], "New-synthetic-password!")
    auth.reset_password(user["username"], "User-synthetic-password!")
    auth.update_user(admin["id"], user["id"], is_active=False)
    journal = tmp_path / "current-journal.json"
    export_recovery_journal(settings.data_dir, journal)
    target = tmp_path / "restored"
    result = restore(backup, journal, target)
    assert result["passwords_invalidated"] == 2
    restored_settings = Settings(data_dir=target, start_workers=False)
    recovered = AuthService(Database(restored_settings), restored_settings)
    for password in (PASSWORD, "New-synthetic-password!"):
        with pytest.raises(AuthError) as error:
            recovered.login(admin["username"], password, "127.0.0.1")
        assert error.value.status_code == 401
    recovered.reset_password(admin["username"], "Fresh-synthetic-password!")
    assert recovered.login(admin["username"], "Fresh-synthetic-password!", "127.0.0.1")
    recovered.reset_password(user["username"], "Fresh-user-password!")
    with pytest.raises(AuthError):
        recovered.login(user["username"], "Fresh-user-password!", "127.0.0.1")
    assert not recovered.get_user(user["id"])["is_active"]
    # Reset invalidation itself must survive a subsequent journal and old restore.
    newer = tmp_path / "restored-journal.json"
    export_recovery_journal(target, newer)
    target_again = tmp_path / "restored-again"
    assert restore(backup, newer, target_again)["passwords_invalidated"] == 2


def test_old_admin_role_and_limits_do_not_return(workspace, tmp_path):
    settings, _, auth, _, admin, user = workspace
    with workspace[1].transaction() as connection:
        connection.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    backup = tmp_path / "snapshot"
    create_backup(settings.data_dir, backup)
    auth.update_user(admin["id"], user["id"], daily_quota=3, max_file_bytes=1_048_576)
    with workspace[1].transaction() as connection:
        connection.execute("UPDATE users SET is_admin=0 WHERE id=?", (user["id"],))
    journal = tmp_path / "current-journal.json"
    export_recovery_journal(settings.data_dir, journal)
    target = tmp_path / "restored"
    restore(backup, journal, target)
    with connect(target) as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE id=?", (user["id"],)
        ).fetchone()
        assert (row["is_admin"], row["daily_quota"], row["max_file_bytes"]) == (
            0,
            3,
            1_048_576,
        )


def test_pending_jobs_require_manual_retry_and_expired_payload_is_filtered(
    workspace, tmp_path
):
    queued = add_job(workspace, succeeded=False)
    running = add_job(workspace, succeeded=False)
    expired = add_job(workspace)
    with workspace[1].transaction() as connection:
        connection.execute(
            "UPDATE jobs SET status='running' WHERE id=?", (running["id"],)
        )
        connection.execute(
            "UPDATE job_attempts SET state='running' WHERE job_id=?", (running["id"],)
        )
        connection.execute(
            "UPDATE jobs SET expires_at=? WHERE id=?", (time.time() - 1, expired["id"])
        )
    backup, journal = bundle(workspace, tmp_path)
    target = tmp_path / "restored"
    assert restore(backup, journal, target)["jobs_interrupted"] == 2
    assert not (target / "jobs" / expired["id"]).exists()
    with connect(target) as connection:
        assert (
            connection.execute(
                "SELECT status FROM jobs WHERE id=?", (queued["id"],)
            ).fetchone()[0]
            == "failed"
        )
        assert (
            connection.execute(
                "SELECT state FROM job_attempts WHERE job_id=?", (running["id"],)
            ).fetchone()[0]
            == "interrupted"
        )
        assert (
            connection.execute(
                "SELECT error_code FROM job_attempts WHERE job_id=?", (queued["id"],)
            ).fetchone()[0]
            == "backup_restored"
        )


def test_wal_commits_are_backed_up_and_scratch_is_excluded(workspace, tmp_path):
    settings, db, _, jobs, _, user = workspace
    with db.connect() as keeper:
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("SELECT COUNT(*) FROM users").fetchone()
        add_job(workspace)
        with db.transaction() as connection:
            connection.execute(
                "UPDATE users SET daily_quota=42 WHERE id=?", (user["id"],)
            )
        assert (settings.data_dir / "workspace.sqlite3-wal").stat().st_size > 0
        jobs._write(jobs.jobs_dir / "orphan-scratch", b"not a canonical artifact")
        backup, _ = bundle(workspace, tmp_path)
        with connect(backup) as snapshot:
            assert (
                snapshot.execute(
                    "SELECT daily_quota FROM users WHERE id=?", (user["id"],)
                ).fetchone()[0]
                == 42
            )
        assert not (backup / "jobs" / "orphan-scratch").exists()
        assert not list(backup.glob("*-wal"))


def test_running_worker_lock_blocks_backup_and_journal(workspace, tmp_path):
    root = workspace[0].data_dir
    with FileLock(str(root / "worker.lock"), mode=0o600):
        with pytest.raises(RecoveryError, match="Stop the service"):
            create_backup(root, tmp_path / "snapshot")
        with pytest.raises(RecoveryError, match="Stop the service"):
            export_recovery_journal(root, tmp_path / "journal.json")
    assert not (tmp_path / "snapshot").exists()


def test_active_database_writer_blocks_snapshot(workspace, tmp_path):
    with workspace[1].transaction():
        with pytest.raises(RecoveryError, match="Backup failed"):
            create_backup(workspace[0].data_dir, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()


def test_mandatory_independent_latest_journal_and_explicit_confirmation(
    workspace, tmp_path
):
    backup, journal = bundle(workspace, tmp_path)
    with pytest.raises(RecoveryError, match="Confirm"):
        restore_backup(backup, tmp_path / "target", journal)
    with pytest.raises(RecoveryError):
        restore(backup, tmp_path / "missing.json", tmp_path / "target")
    with pytest.raises(RecoveryError, match="snapshot cannot"):
        export_recovery_journal(backup, tmp_path / "unsafe.json")
    stale = tmp_path / "stale.json"
    export_recovery_journal(workspace[0].data_dir, stale)
    newer_backup = tmp_path / "later-snapshot"
    create_backup(workspace[0].data_dir, newer_backup)
    with pytest.raises(RecoveryError, match="predates"):
        restore(newer_backup, stale, tmp_path / "target")
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "corruption",
        "missing",
        "extra",
        "traversal",
        "symlink",
        "hardlink",
        "directory_symlink",
        "size",
        "unlisted_required",
    ],
)
def test_corrupt_missing_injected_and_unsafe_snapshots_fail_closed(
    workspace, tmp_path, mutation
):
    job = add_job(workspace)
    backup, journal = bundle(workspace, tmp_path)
    source = backup / "jobs" / job["id"] / "source.txt"
    if mutation == "corruption":
        source.write_bytes(b"tampered")
    elif mutation == "missing":
        source.unlink()
    elif mutation == "extra":
        extra = backup / "injected.txt"
        extra.write_text("injected")
        extra.chmod(0o600)
    elif mutation == "symlink":
        source.unlink()
        source.symlink_to(journal)
    elif mutation == "hardlink":
        source.unlink()
        os.link(journal, source)
    elif mutation == "directory_symlink":
        path = backup / "jobs" / job["id"]
        saved = backup / "moved"
        path.rename(saved)
        path.symlink_to(saved, target_is_directory=True)
    else:
        manifest = json.loads((backup / "manifest.json").read_text())
        record = next(
            row for row in manifest["files"] if row["path"].endswith("source.txt")
        )
        if mutation == "traversal":
            record["path"] = "../../escaped"
        elif mutation == "size":
            record["size"] = 10**20
        else:
            manifest["files"].remove(record)
            source.unlink()
        rewrite_json(backup / "manifest.json", manifest)
    with pytest.raises(RecoveryError):
        restore(backup, journal, tmp_path / "target")
    assert not (tmp_path / "target").exists()
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "hash",
        "lineage",
        "timestamp",
        "unknown_field",
        "invalid_identifier",
        "duplicate",
        "missing_ledger",
    ],
)
def test_invalid_journals_fail_closed(workspace, tmp_path, mutation):
    settings, _, auth, _, admin, user = workspace
    auth.update_user(admin["id"], user["id"], is_active=False)
    backup, journal = bundle(workspace, tmp_path)

    def change(value):
        if mutation == "lineage":
            value["source_id"] = "0" * 32
        elif mutation == "timestamp":
            value["exported_at"] = 1e100
        elif mutation == "unknown_field":
            value["password_hash"] = "injected"
        elif mutation == "invalid_identifier":
            value["accounts"][0]["user_id"] = "../escape"
        elif mutation == "duplicate":
            value["accounts"].append(value["accounts"][0])
        elif mutation == "missing_ledger":
            value["account_revocations"] = []

    rewrite_journal(journal, change)
    if mutation == "hash":
        envelope = json.loads(journal.read_text())
        envelope["sha256"] = "0" * 64
        rewrite_json(journal, envelope)
    with pytest.raises(RecoveryError):
        restore(backup, journal, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_backup_or_restore_cannot_replace_existing_data(workspace, tmp_path):
    backup, journal = bundle(workspace, tmp_path)
    with pytest.raises(RecoveryError, match="already exists"):
        create_backup(workspace[0].data_dir, backup)
    target = tmp_path / "existing"
    target.mkdir(mode=0o700)
    marker = target / "keep"
    marker.write_text("untouched")
    with pytest.raises(RecoveryError, match="empty"):
        restore(backup, journal, target)
    assert marker.read_text() == "untouched"
    with pytest.raises(RecoveryError, match="separate trees"):
        create_backup(workspace[0].data_dir, workspace[0].data_dir / "backup")
    empty = tmp_path / "empty"
    empty.mkdir(mode=0o700)
    restore(backup, journal, empty)
    assert (empty / "workspace.sqlite3").is_file()


def test_symlink_source_lock_and_nonprivate_artifacts_are_rejected(workspace, tmp_path):
    job = add_job(workspace)
    source = workspace[3].jobs_dir / job["id"] / "source.txt"
    source.unlink()
    source.symlink_to(tmp_path / "missing")
    with pytest.raises(RecoveryError):
        create_backup(workspace[0].data_dir, tmp_path / "snapshot")
    source.unlink()
    workspace[3]._write(source, b"synthetic backup")
    lock = workspace[0].data_dir / "worker.lock"
    lock.unlink(missing_ok=True)
    lock.symlink_to(tmp_path / "outside")
    with pytest.raises(RecoveryError, match="symbolic"):
        create_backup(workspace[0].data_dir, tmp_path / "snapshot")
    assert not (tmp_path / "outside").exists()
    lock.unlink()
    backup, journal = bundle(workspace, tmp_path)
    journal.chmod(0o644)
    with pytest.raises(RecoveryError, match="private"):
        restore(backup, journal, tmp_path / "target")


def test_manifest_is_bounded_and_duplicate_keys_rejected(workspace, tmp_path):
    backup, journal = bundle(workspace, tmp_path)
    manifest = backup / "manifest.json"
    manifest.write_bytes(b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(RecoveryError, match="duplicate"):
        restore(backup, journal, tmp_path / "target")
    with manifest.open("wb") as output:
        output.truncate(17 * 1024 * 1024)
    with pytest.raises(RecoveryError, match="size bound"):
        restore(backup, journal, tmp_path / "target")


def test_filtering_disabled_documents_survives_later_reactivation(workspace, tmp_path):
    settings, _, auth, _, admin, user = workspace
    job = add_job(workspace)
    backup = tmp_path / "snapshot"
    create_backup(settings.data_dir, backup)
    auth.update_user(admin["id"], user["id"], is_active=False)
    journal = tmp_path / "current.json"
    export_recovery_journal(settings.data_dir, journal)
    target = tmp_path / "restored"
    restore(backup, journal, target)
    restored_settings = Settings(data_dir=target, start_workers=False)
    recovered = AuthService(Database(restored_settings), restored_settings)
    recovered.update_user(admin["id"], user["id"], is_active=True)
    newer = tmp_path / "newer.json"
    export_recovery_journal(target, newer)
    target_again = tmp_path / "restored-again"
    restore(backup, newer, target_again)
    assert not (target_again / "jobs" / job["id"]).exists()
    with connect(target_again) as connection:
        assert (
            connection.execute(
                "SELECT is_active FROM users WHERE id=?", (user["id"],)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT 1 FROM jobs WHERE id=?", (job["id"],)).fetchone()
            is None
        )


def test_source_hash_and_parent_traversal_fail_closed(workspace, tmp_path):
    job = add_job(workspace)
    source = workspace[3].jobs_dir / job["id"] / "source.txt"
    source.write_bytes(b"modified source!")
    with pytest.raises(RecoveryError, match="recorded job"):
        create_backup(workspace[0].data_dir, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()
    with pytest.raises(RecoveryError, match="traversal"):
        create_backup(
            workspace[0].data_dir, tmp_path / "unused" / ".." / "live" / "backup"
        )


def test_platform_without_private_permissions_fails_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from markitdown_web import recovery

    # Replace only this module's OS reference; do not change pathlib/pytest os.name.
    monkeypatch.setattr(recovery, "os", SimpleNamespace(name="nt"))
    with pytest.raises(RecoveryError, match="POSIX"):
        create_backup(tmp_path / "live", tmp_path / "backup")
    with pytest.raises(RecoveryError, match="POSIX"):
        export_recovery_journal(tmp_path / "live", tmp_path / "journal.json")
    with pytest.raises(RecoveryError, match="POSIX"):
        restore(tmp_path / "backup", tmp_path / "journal.json", tmp_path / "restored")


def test_missing_known_tombstone_cannot_be_hidden_by_newer_timestamp(
    workspace, tmp_path
):
    job = add_job(workspace)
    workspace[3].delete_job(workspace[5]["id"], job["id"])
    backup, journal = bundle(workspace, tmp_path)
    rewrite_journal(journal, lambda value: value.update(job_tombstones=[]))
    with pytest.raises(RecoveryError, match="known tombstone"):
        restore(backup, journal, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_injected_database_trigger_is_rejected_even_with_matching_manifest(
    workspace, tmp_path
):
    backup, journal = bundle(workspace, tmp_path)
    with connect(backup) as connection:
        connection.execute(
            "CREATE TRIGGER injected AFTER DELETE ON sessions BEGIN UPDATE users SET is_admin=1; END"
        )
    manifest = json.loads((backup / "manifest.json").read_text())
    database = (backup / "workspace.sqlite3").read_bytes()
    record = next(
        row for row in manifest["files"] if row["path"] == "workspace.sqlite3"
    )
    record.update(size=len(database), sha256=hashlib.sha256(database).hexdigest())
    rewrite_json(backup / "manifest.json", manifest)
    with pytest.raises(RecoveryError, match="triggers"):
        restore(backup, journal, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_missing_job_ledger_fails_without_upgrading_or_exporting_partial_state(
    workspace, tmp_path
):
    with workspace[1].transaction() as connection:
        connection.execute("DROP TABLE job_tombstones")
    with pytest.raises(RecoveryError, match="ledger schema"):
        create_backup(workspace[0].data_dir, tmp_path / "snapshot")
    with pytest.raises(RecoveryError, match="ledger schema"):
        export_recovery_journal(workspace[0].data_dir, tmp_path / "journal.json")
    assert not (tmp_path / "snapshot").exists()
    assert not (tmp_path / "journal.json").exists()


def test_cli_backup_journal_restore_and_required_confirmation(
    workspace, tmp_path, monkeypatch, capsys
):
    from markitdown_web.__main__ import main

    backup = tmp_path / "snapshot"
    journal = tmp_path / "journal.json"
    target = tmp_path / "restored"
    old_umask = os.umask(0o022)
    try:
        for arguments in (
            [
                "backup",
                "--data-dir",
                str(workspace[0].data_dir),
                "--backup-dir",
                str(backup),
            ],
            [
                "export-recovery-journal",
                "--data-dir",
                str(workspace[0].data_dir),
                "--journal-file",
                str(journal),
            ],
        ):
            monkeypatch.setattr("sys.argv", ["markitdown-web", *arguments])
            main()
            assert isinstance(json.loads(capsys.readouterr().out), dict)
        arguments = [
            "restore",
            "--backup-dir",
            str(backup),
            "--journal-file",
            str(journal),
            "--target-dir",
            str(target),
        ]
        monkeypatch.setattr("sys.argv", ["markitdown-web", *arguments])
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2
        assert not target.exists()
        capsys.readouterr()
        monkeypatch.setattr(
            "sys.argv", ["markitdown-web", *arguments, "--confirm-latest-journal"]
        )
        main()
        output = capsys.readouterr().out
        assert json.loads(output)["sessions_invalidated"] == 2
        assert PASSWORD not in output and "password_hash" not in output
    finally:
        os.umask(old_umask)
