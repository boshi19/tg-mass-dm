"""发送历史与账号级配额。"""

import datetime as dt
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS send_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL,
    target TEXT NOT NULL,
    status TEXT NOT NULL,
    failure_category TEXT NOT NULL DEFAULT '',
    message_preview TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_send_history_account_day
ON send_history(account, created_at);
CREATE INDEX IF NOT EXISTS idx_send_history_account_target_status
ON send_history(account, target, status);
"""


def history_path(config_dir: Path) -> Path:
    return Path(config_dir) / "send_history.sqlite3"


def init_history(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


def _today_range() -> tuple[str, str]:
    today = dt.date.today()
    start = dt.datetime.combine(today, dt.time.min).isoformat(timespec="seconds")
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min).isoformat(timespec="seconds")
    return start, end


def count_success_today(db_path: str | Path, account: str) -> int:
    init_history(db_path)
    start, end = _today_range()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM send_history
            WHERE account = ? AND status = 'success' AND created_at >= ? AND created_at < ?
            """,
            (account, start, end),
        ).fetchone()
    return int(row[0] if row else 0)


def successful_targets_today(db_path: str | Path, account: str) -> set[str]:
    init_history(db_path)
    start, end = _today_range()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT target FROM send_history
            WHERE account = ? AND status = 'success' AND created_at >= ? AND created_at < ?
            """,
            (account, start, end),
        ).fetchall()
    return {str(row[0]) for row in rows}


def record_send(
    db_path: str | Path,
    account: str,
    target: str,
    status: str,
    failure_category: str = "",
    message_preview: str = "",
    error: str = "",
) -> None:
    init_history(db_path)
    preview = (message_preview or "")[:160]
    now = dt.datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO send_history
            (account, target, status, failure_category, message_preview, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (account, target, status, failure_category, preview, error[:500], now),
        )
