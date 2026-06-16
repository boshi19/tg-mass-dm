# tg-mass-dm 重构方案

> 版本：v3.0 | 基于 send.py v2.1 分析

---

## 1. 函数依赖关系图

```
main()
 ├─ load_config(config_path) → cfg (dict)
 │    └─ cfg["_config_dir"] = Path(config_path).parent.resolve()  ← 运行时注入
 ├─ cfg.get("schedule") → wait_until_scheduled()
 ├─ asyncio.run(send_messages(cfg, dry_run))
 │    ├─ cfg["messages_file"] → load_messages() → list[str]
 │    ├─ cfg["usernames_file"] → load_targets() → list[str]
 │    ├─ TelegramClient(cfg["session_file"], cfg["api_id"], cfg["api_hash"])
 │    ├─ 主循环 (for target in targets):
 │    │    ├─ random.choice(messages)
 │    │    ├─ tail_cfg["enabled"]? → append_random_tail() → str
 │    │    ├─ client.send_message(username, final_text)
 │    │    ├─ FloodWaitError → asyncio.sleep + retry
 │    │    ├─ 永久错误 → _remove_invalid_target() → bool
 │    │    └─ random_sleep(delay_cfg)
 │    └─ finally: safe_disconnect(client)
```

**耦合点诊断:**

| 耦合 | 严重度 | 问题 |
|------|--------|------|
| `_config_dir` 运行时注入 dict | 🔴 高 | 隐式契约，难跟踪 |
| `sys.exit()` 分散在 load_* 函数 | 🔴 高 | 工具函数不应直接终止进程 |
| `cfg` dict 全局传递 | 🟡 中 | 无类型安全，拼写错误无提示 |
| `print()` 直接输出 | 🟡 中 | 无法重定向、无日志等级 |

---

## 2. 模块拆分方案

```
tg-mass-dm/
├── main.py          # CLI 入口 + 流程编排
├── config.py        # 配置加载、校验、类型定义
├── targets.py       # 目标列表管理
├── messages.py      # 文案池 + 随机尾部
├── scheduler.py     # 宏观定时 + 微观延时
├── sender.py        # 核心发送逻辑 (Telethon 交互)
├── config.yaml      # 用户配置（不变）
├── usernames.txt    # 目标用户列表（不变）
├── messages.txt     # 文案模板（不变）
├── requirements.txt # 依赖（不变）
├── run.bat          # 启动脚本（适配 main.py）
├── dry-run.bat      # 测试脚本（适配 main.py）
├── scheduled.bat    # 定时脚本（适配 main.py）
└── tests/
    ├── __init__.py
    ├── test_config.py
    ├── test_targets.py
    ├── test_messages.py
    ├── test_scheduler.py
    └── test_sender.py
```

---

## 3. 各模块接口契约

### 3.1 `config.py`

```python
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class ScheduleConfig:
    start_time: str | None = None       # "HH:MM"
    timezone: str = "Asia/Shanghai"
    wait_if_past: bool = False

@dataclass
class DelayConfig:
    min: float = 8.0
    max: float = 25.0

@dataclass
class RandomTailConfig:
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
        self.session_path = self.config_dir / self.session_file
        self.messages_path = self.config_dir / self.messages_file
        self.usernames_path = self.config_dir / self.usernames_file


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
    ...
```

### 3.2 `targets.py`

```python
from pathlib import Path

def load_targets(file_path: str | Path) -> list[str]:
    """
    加载目标用户列表，去重，跳过空行和 # 注释。
    
    参数:
        file_path: str | Path
    返回:
        list[str] - 去重后的目标用户名列表
    抛出:
        FileNotFoundError - 文件不存在
        ValueError - 文件为空
    """
    ...

def remove_target(file_path: str | Path, username: str) -> bool:
    """
    从目标列表文件中移除指定用户名（原地修改）。
    
    参数:
        file_path: str | Path
        username: str - 要移除的用户名
    返回:
        bool - True=已移除, False=未找到或文件不存在
    """
    ...
```

### 3.3 `messages.py`

```python
from pathlib import Path

def load_messages(file_path: str | Path) -> list[str]:
    """
    加载文案池，跳过空行和 # 注释。
    
    参数:
        file_path: str | Path
    返回:
        list[str] - 文案列表
    抛出:
        FileNotFoundError - 文件不存在
        ValueError - 文案池为空
    """
    ...

def append_random_tail(text: str, style: str = "dots") -> str:
    """
    在消息末尾追加随机标识。
    
    参数:
        text: str - 原始消息
        style: str - "dots" (随机 ".") | "ref" ([ref:xxxxxxx])
    返回:
        str - 带随机尾部的消息
    """
    ...
```

### 3.4 `scheduler.py`

```python
from .config import ScheduleConfig, DelayConfig

def wait_until_scheduled(schedule: ScheduleConfig) -> None:
    """
    如果配置了定时，在指定时间前阻塞等待。
    
    参数:
        schedule: ScheduleConfig
    """
    ...

def random_sleep(delay: DelayConfig) -> None:
    """
    在 [min, max] 范围内随机等待，长间隔时每分钟打印倒计时。
    
    参数:
        delay: DelayConfig
    """
    ...
```

### 3.5 `sender.py`

```python
import asyncio
from dataclasses import dataclass
from .config import AppConfig

@dataclass
class SendResult:
    """发送结果汇总"""
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    removed: int = 0
    removed_usernames: list[str] = field(default_factory=list)

async def send_messages(cfg: AppConfig, dry_run: bool = False) -> SendResult:
    """
    连接 Telethon 并逐条发送私信。
    
    参数:
        cfg: AppConfig - 应用配置（类型安全）
        dry_run: bool - 测试模式，不实际发送
    返回:
        SendResult - 发送结果汇总
    """
    ...
```

### 3.6 `main.py`

```python
import asyncio
import argparse
from .config import load_config
from .scheduler import wait_until_scheduled
from .sender import send_messages

def main():
    """
    CLI 入口：
    1. 解析命令行参数
    2. 加载配置
    3. 等待定时（如配置）
    4. 执行发送
    """
    parser = argparse.ArgumentParser(...)
    args = parser.parse_args()
    
    cfg = load_config(args.config)
    
    if not args.now:
        wait_until_scheduled(cfg.schedule)
    
    result = asyncio.run(send_messages(cfg, dry_run=args.dry_run))
    
    # 输出汇总
    print(f"成功: {result.sent}, 失败: {result.failed}, ...")

if __name__ == "__main__":
    main()
```

---

## 4. 耦合解耦策略

### 4.1 `_config_dir` 注入 → `AppConfig.config_dir`

**旧**: `cfg["_config_dir"] = str(Path(args.config).parent.resolve())`
**新**: `AppConfig.__post_init__` 自动计算所有基于 config_dir 的路径

### 4.2 `sys.exit()` 移除

**旧**: `load_config` / `load_messages` / `load_targets` 内部调用 `sys.exit(1)`
**新**: 抛出 Python 异常（`FileNotFoundError`, `ValueError`），由 `main()` 统一捕获并退出

### 4.3 `print()` → 结构化日志

**旧**: 全 `print()` 输出
**新**: 保持 `print()` 用于用户可见输出（命令行工具），但函数返回结构化数据（`SendResult`），方便未来接入日志框架

---

## 5. 向后兼容

- `config.yaml` 格式不变
- `usernames.txt` / `messages.txt` 格式不变
- BAT 脚本仅改 `python send.py` → `python main.py`
- Session 文件路径不变
- 所有命令行参数不变
