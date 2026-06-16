"""pytest 共享 fixtures 和配置"""
import pytest
from pathlib import Path


@pytest.fixture
def sample_config_dict():
    """返回一份最小可用的配置字典"""
    return {
        "api_id": 123456,
        "api_hash": "abc123def456",
        "session_file": "test_session",
        "usernames_file": "usernames.txt",
        "messages_file": "messages.txt",
        "schedule": {
            "start_time": "08:30",
            "timezone": "Asia/Shanghai",
            "wait_if_past": False,
        },
        "delay": {"min": 8, "max": 25},
        "daily_limit": 35,
        "random_tail": {"enabled": True, "style": "dots"},
        "dry_run": False,
    }


@pytest.fixture
def tmp_yaml_config(tmp_path, sample_config_dict):
    """在临时目录中创建一个最小的 YAML 配置文件"""
    import yaml
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.dump(sample_config_dict, f, allow_unicode=True)
    return str(config_file)


@pytest.fixture
def tmp_yaml_config_missing(tmp_path):
    """创建缺少必填字段的 YAML 配置"""
    import yaml
    config_file = tmp_path / "config_missing.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.dump({"api_id": 123}, f, allow_unicode=True)
    return str(config_file)


@pytest.fixture
def tmp_usernames_file(tmp_path):
    """创建临时目标用户文件"""
    content = "\n".join([
        "user1",
        "user2",
        "user3",
        "",
        "# 这是注释",
        "user2",
        "user4",
    ])
    f = tmp_path / "usernames.txt"
    f.write_text(content, encoding="utf-8")
    return str(f)


@pytest.fixture
def tmp_messages_file(tmp_path):
    """创建临时文案池文件"""
    content = "\n".join([
        "你好，这是第一条消息",
        "第二条消息模板",
        "",
        "# 注释行",
        "第三条消息",
    ])
    f = tmp_path / "messages.txt"
    f.write_text(content, encoding="utf-8")
    return str(f)


@pytest.fixture
def tmp_empty_file(tmp_path):
    """创建空的临时文件"""
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    return str(f)


@pytest.fixture
def tmp_comments_only_file(tmp_path):
    """创建只有注释和空行的文件"""
    content = "\n".join([
        "# 注释1",
        "",
        "# 注释2",
        "",
    ])
    f = tmp_path / "comments_only.txt"
    f.write_text(content, encoding="utf-8")
    return str(f)
