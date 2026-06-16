"""test_targets.py - 目标列表管理模块的 pytest 测试"""
import pytest
from targets import load_targets, remove_target


class TestLoadTargets:
    """测试目标列表加载"""

    def test_load_with_unique(self, tmp_usernames_file):
        targets = load_targets(tmp_usernames_file)
        assert targets == ["user1", "user2", "user3", "user4"]
        # 验证 user2 只出现一次（去重）
        assert targets.count("user2") == 1

    def test_skips_comments_and_blank(self, tmp_usernames_file):
        targets = load_targets(tmp_usernames_file)
        # 不应包含注释或空行内容
        assert "#" not in targets
        assert "" not in targets
        assert len(targets) == 4  # user1, user2, user3, user4

    def test_empty_file_raises(self, tmp_empty_file):
        with pytest.raises(ValueError, match="目标列表为空"):
            load_targets(tmp_empty_file)

    def test_comments_only_file_raises(self, tmp_comments_only_file):
        with pytest.raises(ValueError, match="目标列表为空"):
            load_targets(tmp_comments_only_file)

    def test_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_targets("/nonexistent/usernames.txt")

    def test_returns_list_of_strings(self, tmp_usernames_file):
        targets = load_targets(tmp_usernames_file)
        assert isinstance(targets, list)
        assert all(isinstance(t, str) for t in targets)


class TestRemoveTarget:
    """测试目标用户移除"""

    def test_remove_existing(self, tmp_usernames_file):
        result = remove_target(tmp_usernames_file, "user1")
        assert result is True
        # 验证 user1 已从文件移除
        remaining = load_targets(tmp_usernames_file)
        assert "user1" not in remaining

    def test_remove_not_found(self, tmp_usernames_file):
        result = remove_target(tmp_usernames_file, "nonexistent_user")
        assert result is False

    def test_remove_nonexistent_file(self):
        result = remove_target("/nonexistent/file.txt", "user1")
        assert result is False

    def test_remove_preserves_other_users(self, tmp_usernames_file):
        remove_target(tmp_usernames_file, "user2")
        remaining = load_targets(tmp_usernames_file)
        assert remaining == ["user1", "user3", "user4"]
