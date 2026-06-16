# config.py - 配置加载与类型定义

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ScheduleConfig:
    """宏观定时配置"""
    start_time: str | None = None       # "HH:MM"
    timezone: str = "Asia/Shanghai"
    wait_if_past: bool = False


@dataclass
class DelayConfig:
    """微观随机延时配置"""
    min: float = 8.0
    max: float = 25.0


@dataclass
class RandomTailConfig:
    """随机尾部配置"""
    enabled: bool = True
    style: str = "dots"                 # "dots" | "ref"


@dataclass
class AppConfig:
    """完整应用配置，类型安全"""
    api_id: int
    api_hash: str
    session_file: str                   # 不含 .session 后缀
    usernames_file: str = "usernames.txt"
    messages_file: str = "messages.txt"
    proxy: dict | None = None            # 代理设置，如 {"type":"socks5","host":"127.0.0.1","port":1080}
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    delay: DelayConfig = field(default_factory=DelayConfig)
    random_tail: RandomTailConfig = field(default_factory=RandomTailConfig)
    daily_limit: int = 20
    dry_run: bool = False

    # 以下为运行时计算字段
    config_dir: Path = field(default_factory=Path)
    session_path: Path = field(init=False)
    messages_path: Path = field(init=False)
    usernames_path: Path = field(init=False)

    def __post_init__(self):
        import sys
        # 判断是否为 PyInstaller 打包环境
        if getattr(sys, 'frozen', False):
            # 打包后的 `.exe` 实际所在物理目录（用户数据、配置在此存放）
            exe_dir = Path(sys.executable).resolve().parent
            if self.config_dir is None or str(self.config_dir) == ".":
                self.config_dir = exe_dir
        else:
            # 源码运行模式
            if self.config_dir is None or str(self.config_dir) == ".":
                self.config_dir = Path(__file__).resolve().parent

        self.session_path = self.config_dir / self.session_file
        self.messages_path = self.config_dir / self.messages_file
        self.usernames_path = self.config_dir / self.usernames_file

    def to_dict(self) -> dict:
        """序列化为可写回 YAML 的 dict（与 config.yaml 结构一致）。"""
        result = {
            "api_id": self.api_id,
            "api_hash": self.api_hash,
            "session_file": self.session_file,
            "usernames_file": self.usernames_file,
            "messages_file": self.messages_file,
            "schedule": {
                "start_time": self.schedule.start_time or "",
                "timezone": self.schedule.timezone,
                "wait_if_past": self.schedule.wait_if_past,
            },
            "delay": {"min": self.delay.min, "max": self.delay.max},
            "daily_limit": self.daily_limit,
            "random_tail": {
                "enabled": self.random_tail.enabled,
                "style": self.random_tail.style,
            },
            "dry_run": self.dry_run,
        }
        if self.proxy:
            result["proxy"] = self.proxy
        return result


def _env_override(raw: dict) -> dict:
    """
    可选：从环境变量/.env 覆盖敏感字段，避免 api_hash 明文写死在 yaml。

    支持的环境变量：
      TG_API_ID / TG_API_HASH / TG_SESSION_FILE
    .env 文件需自行用 python-dotenv 加载（避免新增依赖，此处仅读 os.environ）。
    """
    if os.environ.get("TG_API_ID"):
        try:
            raw["api_id"] = int(os.environ["TG_API_ID"])
        except ValueError:
            pass
    if os.environ.get("TG_API_HASH"):
        raw["api_hash"] = os.environ["TG_API_HASH"]
    if os.environ.get("TG_SESSION_FILE"):
        raw["session_file"] = os.environ["TG_SESSION_FILE"]
    return raw


def load_config(config_path: str) -> AppConfig:
    """
    从 YAML 文件加载并校验配置，返回类型安全的 AppConfig。

    参数:
        config_path: str - YAML 配置文件路径
    返回:
        AppConfig - 校验后的配置对象
    抛出:
        FileNotFoundError - 配置文件不存在
        ValueError - 必填字段缺失
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"[错误] 配置文件不存在: {path.resolve()}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    raw = _env_override(raw)

    # 必填字段校验
    required = ["api_id", "api_hash", "session_file", "usernames_file", "messages_file"]
    missing = [k for k in required if not raw.get(k)]
    if missing:
        raise ValueError(f"[错误] 配置缺少必填字段: {', '.join(missing)}")

    # 构建 ScheduleConfig
    schedule_raw = raw.get("schedule") or {}
    schedule = ScheduleConfig(
        start_time=schedule_raw.get("start_time") or None,
        timezone=schedule_raw.get("timezone", "Asia/Shanghai"),
        wait_if_past=schedule_raw.get("wait_if_past", False),
    )

    # 构建 DelayConfig
    delay_raw = raw.get("delay") or {}
    delay = DelayConfig(
        min=float(delay_raw.get("min", 8)),
        max=float(delay_raw.get("max", 25)),
    )

    # 构建 RandomTailConfig
    tail_raw = raw.get("random_tail") or {}
    random_tail = RandomTailConfig(
        enabled=bool(tail_raw.get("enabled", True)),
        style=str(tail_raw.get("style", "dots")),
    )

    import sys
    if getattr(sys, 'frozen', False):
        config_dir = Path(sys.executable).resolve().parent
    else:
        config_dir = Path(config_path).parent.resolve()

    # 构建 proxy
    proxy_raw = raw.get("proxy")
    proxy = None
    if proxy_raw and isinstance(proxy_raw, dict):
        proxy = {k: v for k, v in proxy_raw.items() if v}

    return AppConfig(
        api_id=int(raw["api_id"]),
        api_hash=str(raw["api_hash"]),
        session_file=str(raw["session_file"]),
        usernames_file=str(raw.get("usernames_file", "usernames.txt")),
        messages_file=str(raw.get("messages_file", "messages.txt")),
        proxy=proxy,
        schedule=schedule,
        delay=delay,
        random_tail=random_tail,
        daily_limit=int(raw.get("daily_limit", 20)),
        dry_run=bool(raw.get("dry_run", False)),
        config_dir=config_dir,
    )


def save_config(config_path: str, cfg: AppConfig) -> None:
    """把 AppConfig 写回 YAML 文件（保留中文注释头）。"""
    path = Path(config_path)
    header = (
        "# ═══════════════════════════════════════════\n"
        "#  Telegram 批量私信发送器 - 配置文件 (WebUI 编辑)\n"
        "# ═══════════════════════════════════════════\n\n"
    )
    body = yaml.dump(
        cfg.to_dict(),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    path.write_text(header + body, encoding="utf-8")
