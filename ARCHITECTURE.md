# tg-mass-dm 架构分析与模块拆分方案

## 一、现状概述

`send.py`（~446行）是一个 Telegram 批量私信发送器。功能包括：
- YAML 配置加载与校验
- 文案池与目标列表加载
- 宏观定时调度（每日指定时间启动）
- 微观随机延时（模拟人类行为）
- 随机消息尾部（防 Hash 重复/风控）
- 异常安全：FloodWait 自动等待重试、PeerFlood 立即终止
- 自动剔除无效目标（隐私限制、非互联系人、RPC 错误）

## 二、函数依赖关系图

```
main()
├── load_config(config_path)           # 返回 cfg dict
│   └── 依赖: yaml, Path, sys
├── wait_until_scheduled(schedule_cfg)  # 阻塞到定时
│   └── 依赖: datetime, ZoneInfo, time
├── send_messages(cfg, dry_run)         # 核心发送
a│   ├── load_messages(file_path)       # → list[str]
│   │   └── 依赖: Path, sys
│   ├── load_targets(file_path)         # → list[str] (去重)
│   │   └── 依赖: Path, sys
│   ├── safe_connect(client)            # 异步连接
│   │   └── 依赖: asyncio, sys
│   ├── safe_disconnect(client)         # 异步断开
│   │   └── 依赖: asyncio
│   ├── random_tail(cfg)                # → str (随机尾部)
│   │   └── 依赖: random
│   ├── random_sleep(delay_cfg)         # 随机休眠
│   │   └── 依赖: random, time
│   └── _remove_invalid_target(path,u)  # → bool (删除用户)
│       └── 依赖: Path
└── (TelegramClient 来自 telethon)
```

## 三、模块拆分方案

### 目标结构

```
tg-mass-dm/
├── main.py          # 入口：参数解析 + 编排调度
├── config.py        # 配置加载与校验
├── targets.py       # 目标管理
├── messages.py      # 文案池
├── scheduler.py     # 定时 + 延时 + 随机尾部
├── sender.py        # 核心发送逻辑（Telethon）
├── config.yaml
├── messages.txt
├── usernames.txt
├── requirements.txt
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_targets.py
    ├── test_messages.py
    ├── test_scheduler.py
    ├── test_sender.py
    └── test_main.py
```

---

### 模块 1: `config.py` — 配置管理

| 项目 | 内容 |
|------|------|
| **函数** | `load_config(config_path: str) -> dict` |
| **输入** | config_path: YAML 文件路径字符串 |
| **输出** | 校验后的配置字典 |
| **副作用** | 配置缺字段时 `sys.exit(1)` |
| **关键字段** | api_id, api_hash, session_file, usernames_file, messages_file |
| **可选字段** | schedule, delay, daily_limit, random_tail, dry_run |

**接口契约：**
```python
def load_config(config_path: str) -> dict:
    """
    加载 YAML 配置并校验必填字段。
    
    Args:
        config_path: YAML 文件路径
        
    Returns:
        dict: 完整配置字典，保证包含所有必填字段
        
    Raises:
        SystemExit(1): 文件不存在或缺少必填字段
    """
```

---

### 模块 2: `targets.py` — 目标管理

| 项目 | 内容 |
|------|------|
| **函数 1** | `load_targets(file_path: str) -> list[str]` |
| **函数 2** | `remove_invalid_target(file_path: str, username: str) -> bool` |

**接口契约：**
```python
def load_targets(file_path: str) -> list[str]:
    """
    加载目标用户列表，去重，过滤空行和注释。
    
    Args:
        file_path: 目标文件路径
        
    Returns:
        list[str]: 去重后的目标用户名列表
        
    Raises:
        SystemExit(1): 文件不存在或列表为空
    """

def remove_invalid_target(file_path: str, username: str) -> bool:
    """
    从目标文件中移除指定用户名。
    
    Args:
        file_path: 目标文件路径
        username: 待移除的用户名
        
    Returns:
        bool: 成功移除返回 True，否则 False
    """
```

---

### 模块 3: `messages.py` — 文案池

| 项目 | 内容 |
|------|------|
| **函数** | `load_messages(file_path: str) -> list[str]` |

**接口契约：**
```python
def load_messages(file_path: str) -> list[str]:
    """
    加载文案池，过滤空行和注释。
    
    Args:
        file_path: 文案文件路径
        
    Returns:
        list[str]: 文案模板列表
        
    Raises:
        SystemExit(1): 文件不存在或列表为空
    """
```

---

### 模块 4: `scheduler.py` — 定时与随机

| 项目 | 内容 |
|------|------|
| **函数 1** | `wait_until_scheduled(schedule_cfg: dict | None) -> None` |
| **函数 2** | `random_sleep(delay_cfg: dict) -> None` |
| **函数 3** | `random_tail(cfg: dict) -> str` |

**接口契约：**
```python
def wait_until_scheduled(schedule_cfg: dict | None) -> None:
    """
    阻塞等待直到到达 schedule 中指定的时间。
    如 schedule_cfg 为 None 或空，立即返回。
    
    Args:
        schedule_cfg: {"start_time": "08:30", "timezone": "Asia/Shanghai", "wait_if_past": bool}
    """

def random_sleep(delay_cfg: dict) -> None:
    """
    在 delay_cfg["min"] 到 delay_cfg["max"] 秒之间随机休眠。
    
    Args:
        delay_cfg: {"min": int, "max": int}
    """

def random_tail(cfg: dict) -> str:
    """
    生成随机尾部字符串。
    
    Args:
        cfg: 完整配置，读取 cfg["random_tail"]
        
    Returns:
        str: 随机尾部（dots 风格返回随机个数的 "·"，ref 风格返回 [ref:xxxxxxxx]）
    """
```

---

### 模块 5: `sender.py` — 核心发送逻辑

| 项目 | 内容 |
|------|------|
| **函数 1** | `safe_connect(client: TelegramClient) -> None` |
| **函数 2** | `safe_disconnect(client: TelegramClient) -> None` |
| **函数 3** | `send_messages(cfg: dict, dry_run: bool = False) -> None` |

**接口契约：**
```python
async def safe_connect(client: TelegramClient) -> None:
    """
    安全连接 Telegram 客户端，失败时退出。
    
    Args:
        client: TelegramClient 实例
        
    Raises:
        SystemExit(1): 连接失败
    """

async def safe_disconnect(client: TelegramClient) -> None:
    """安全断开客户端连接，静默处理异常。"""

async def send_messages(cfg: dict, dry_run: bool = False) -> None:
    """
    核心发送编排函数。
    
    流程:
    1. 调用 load_messages / load_targets
    2. 创建 TelegramClient (cfg["session_file"], cfg["api_id"], cfg["api_hash"])
    3. safe_connect
    4. 遍历目标列表:
       - random_tail() 生成尾部
       - random.choice(messages) 选择文案
       - dry_run 时仅打印
       - 发送 + 异常分类处理
       - random_sleep() 延迟
       - daily_limit 检查
    5. safe_disconnect (finally)
    
    Args:
        cfg: 完整配置字典
        dry_run: 测试模式，仅打印不发送
    """
```

---

### 模块 6: `main.py` — 入口

| 项目 | 内容 |
|------|------|
| **函数** | `main() -> None` |

**接口契约：**
```python
def main() -> None:
    """
    入口函数：
    1. 解析 CLI 参数 (--config, --dry-run, --now)
    2. 调用 config.load_config()
    3. 调用 scheduler.wait_until_scheduled()
    4. 调用 sender.send_messages()
    """
```

---

## 四、模块间调用关系（重构后）

```
main.py
├── import config.load_config
├── import scheduler.wait_until_scheduled
└── import sender.send_messages

sender.py
├── import messages.load_messages
├── import targets.load_targets
├── import targets.remove_invalid_target
├── import scheduler.random_tail
├── import scheduler.random_sleep
├── (TelegramClient)                      # 仅 sender.py 依赖 telethon
├── safe_connect                          # 模块内部
└── safe_disconnect                       # 模块内部

config.py     → 独立，无内部依赖
targets.py    → 独立，无内部依赖
messages.py   → 独立，无内部依赖
scheduler.py  → 独立，无内部依赖
```

## 五、关键设计决策

1. **Telethon 隔离**：仅 `sender.py` 依赖 `telethon`，其余模块纯 Python 标准库
2. **sys.exit 收敛**：仅 `config.py`/`targets.py`/`messages.py`/`sender.py` 的错误会调用 `sys.exit(1)`
3. **全局变量消除**：原 `send.py` 中 logger 和 `_scheduled_time` 等模块级状态随拆分自然内聚
4. **互斥读写安全**：`remove_invalid_target` 对 `usernames.txt` 的写操作与 `load_targets` 的读操作不在并发路径上
5. **向后兼容**：`main.py` 保持相同 CLI 接口（--config, --dry-run, --now）
