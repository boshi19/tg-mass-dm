"""test_config.py - 配置加载模块的 pytest 测试"""
import pytest
from pathlib import Path
from config import load_config, AppConfig, ScheduleConfig, DelayConfig, RandomTailConfig


class TestLoadConfigValid:
    """测试正常配置加载"""

    def test_loads_all_required_fields(self, tmp_yaml_config):
        cfg = load_config(tmp_yaml_config)
        assert cfg.api_id == 123456
        assert cfg.api_hash == "abc123def456"
        assert cfg.session_file == "test_session"
        assert isinstance(cfg, AppConfig)

    def test_schedule_config_parsed(self, tmp_yaml_config):
        cfg = load_config(tmp_yaml_config)
        assert isinstance(cfg.schedule, ScheduleConfig)
        assert cfg.schedule.start_time == "08:30"
        assert cfg.schedule.timezone == "Asia/Shanghai"
        assert cfg.schedule.wait_if_past is False

    def test_delay_config_parsed(self, tmp_yaml_config):
        cfg = load_config(tmp_yaml_config)
        assert isinstance(cfg.delay, DelayConfig)
        assert cfg.delay.min == 8.0
        assert cfg.delay.max == 25.0

    def test_random_tail_config_parsed(self, tmp_yaml_config):
        cfg = load_config(tmp_yaml_config)
        assert isinstance(cfg.random_tail, RandomTailConfig)
        assert cfg.random_tail.enabled is True
        assert cfg.random_tail.style == "dots"

    def test_config_dir_set(self, tmp_yaml_config):
        cfg = load_config(tmp_yaml_config)
        expected = Path(tmp_yaml_config).parent.resolve()
        assert cfg.config_dir == expected

    def test_session_path_computed(self, tmp_yaml_config):
        cfg = load_config(tmp_yaml_config)
        expected = cfg.config_dir / cfg.session_file
        assert cfg.session_path == expected

    def test_default_values_present(self, tmp_yaml_config):
        cfg = load_config(tmp_yaml_config)
        assert cfg.daily_limit == 35
        assert cfg.dry_run is False


class TestLoadConfigErrors:
    """测试配置加载异常"""

    def test_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_missing_required_fields_raises(self, tmp_yaml_config_missing):
        with pytest.raises(ValueError, match="缺少必填字段"):
            load_config(tmp_yaml_config_missing)


class TestAppConfigPostInit:
    """测试 AppConfig __post_init__ 路径计算"""

    def test_post_init_with_relative_paths(self, tmp_path):
        cfg = AppConfig(
            api_id=1, api_hash="x", session_file="s",
            usernames_file="u.txt", messages_file="m.txt",
            config_dir=tmp_path,
        )
        assert cfg.session_path == tmp_path / "s"
        assert cfg.usernames_path == tmp_path / "u.txt"
        assert cfg.messages_path == tmp_path / "m.txt"
