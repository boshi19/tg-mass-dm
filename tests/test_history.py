"""发送历史与账号配额测试。"""

from history import count_success_today, record_send, successful_targets_today


def test_record_and_count_success_today(tmp_path):
    db = tmp_path / "history.sqlite3"

    record_send(db, "acct", "user1", "success", message_preview="hello")
    record_send(db, "acct", "user2", "failed", failure_category="temporary")
    record_send(db, "other", "user3", "success")

    assert count_success_today(db, "acct") == 1
    assert successful_targets_today(db, "acct") == {"user1"}
