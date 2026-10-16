import os
import sqlite3
import subprocess
import sys


def test_role_migration_preserves_accounts_and_results(tmp_path, password_hash):
    path = tmp_path / "migration.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path.as_posix()}"}

    def migrate(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    migrate("upgrade", "0002")
    with sqlite3.connect(path) as db:
        for identity, active, date in [
            ("disabled", False, "2025-01-01"),
            ("first", True, "2025-01-02"),
            ("second", True, "2025-01-03"),
        ]:
            db.execute(
                "INSERT INTO users (id,username,password_hash,is_active,created_at) "
                "VALUES (?,?,?,?,?)",
                (identity, identity, password_hash, active, date),
            )
        db.execute("""INSERT INTO qualification_runs
          (id, source_key, source_text, source_snapshot, extractor, pipeline_version, status,
           result, duration_ms, token_usage, created_at)
          VALUES ('saved-run','saved-key','Existing request','{}','rules','0.1.0',
          'pending_review','{}',0,'{}','2025-01-01')""")
    migrate("upgrade", "head")
    migrate("check")
    with sqlite3.connect(path) as db:
        assert dict(db.execute("SELECT username,role FROM users")) == {
            "disabled": "manager",
            "first": "admin",
            "second": "manager",
        }
        assert (
            db.execute("SELECT source_text FROM qualification_runs").fetchone()[0]
            == "Existing request"
        )
        assert db.execute("SELECT version FROM user_admin_lock WHERE id=1").fetchone()[0] == 0
        assert db.execute(
            "SELECT created_by_user_id, created_by_username FROM qualification_runs"
        ).fetchone() == (None, None)
        assert db.execute("SELECT trigger FROM qualification_runs").fetchone()[0] == "manual"
        assert db.execute(
            "SELECT text_field, portal_key FROM bitrix_source WHERE id=1"
        ).fetchone() == (None, None)
        assert db.execute("SELECT enabled, cursor FROM lead_monitor WHERE id=1").fetchone() == (
            0,
            None,
        )
        db.execute(
            "INSERT INTO users (id,username,password_hash,role,is_active,created_at) "
            "VALUES ('pending','pending',NULL,'manager',1,'2025-01-04')"
        )
    migrate("downgrade", "0002")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT password_hash FROM users WHERE id='pending'").fetchone()[0] == "!"
