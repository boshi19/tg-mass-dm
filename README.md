# tg-mass-dm v4.1-web

## v4.1-web 更新特性

- 增加 Telethon 关键网络调用超时，降低长时间运行后的假死风险。
- 增加任务心跳字段：`last_heartbeat`、`last_action`、`current_target`、`waiting`。
- 随机延时、定时等待、限流等待不再输出具体等待分钟/秒数日志。
- WebUI 可通过 `/api/status` 判断任务是否仍在运行或等待中。


基于 Telethon 个人账户 API 的 Telegram 批量私信发送器。

## 核心特性

- **宏观定时**：设定每天 08:30 自动启动，抓住用户看手机的黄金时间
- **微观随机**：随机延时(8-25s) + 随机文案 + 随机尾部，迷惑机器审查
- **单日硬上限**：默认 20 条/天，达到即停，宁可少发不要被封
- **随机尾部**：每条消息末尾追加随机标识，确保 Hash 值完全不同
- **异常安全**：PeerFlood 立即终止，FloodWait 自动等待重试
- **自动剔除**：遇到永久性失败（隐私限制、非互联系人、RPC 错误等），自动将该目标从 usernames.txt 中删除，下次运行不再尝试，并在日志中输出剔除清单
- **配置化**：所有参数通过 config.yaml 管理，无需改代码

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 获取 API 凭证

1. 访问 [my.telegram.org](https://my.telegram.org)
2. 登录后进入 API Development Tools
3. 创建应用，获取 `api_id` 和 `api_hash`

### 3. 编辑配置

修改 `config.yaml` 中的凭证和参数：

```yaml
api_id: 你的API_ID
api_hash: "你的API_HASH"
session_file: "sessions/你的session文件名"
```

### 4. 准备文件

- `usernames.txt`：每行一个目标用户（@username 或手机号）
- `messages.txt`：每行一条文案模板（随机选择发送）

### 5. 运行

```bash
# 使用默认配置
python send.py

# 测试模式（不实际发送）
python send.py --dry-run

# 跳过定时，立即执行
python send.py --now

# 指定配置文件
python send.py --config my_config.yaml

三个 BAT 文件（双击即用）

文件	           用途
run.bat	        正式发送（跳过定时，立即执行）
dry-run.bat	    测试预览（不实际发送，看看效果）
scheduled.bat	定时模式（按 config.yaml 中的 start_time 等待启动）
```

## 配置说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `api_id` | Telegram API ID | 必填 |
| `api_hash` | Telegram API Hash | 必填 |
| `session_file` | Session 文件路径（不含 .session） | 必填 |
| `usernames_file` | 目标用户列表文件 | usernames.txt |
| `messages_file` | 文案池文件 | messages.txt |
| `schedule.start_time` | 定时启动时间 (HH:MM) | 空=立即执行 |
| `schedule.timezone` | 时区 | Asia/Shanghai |
| `delay.min` | 最小延时（秒） | 8 |
| `delay.max` | 最大延时（秒） | 25 |
| `daily_limit` | 单日发送上限 | 20 |
| `random_tail.enabled` | 是否追加随机尾部 | true |
| `random_tail.style` | 尾部样式 dots/ref | dots |
| `dry_run` | 测试模式 | false |

## 安全建议

- **新号（注册<3个月）**：daily_limit 建议设为 5-10
- **老号**：可适当放宽到 20，但不建议超过 30
- **PeerFloodError**：立即停止，等待 24-48 小时
- **每天只运行一次**：避免短时间内大量发送
- **文案多样化**：messages.txt 至少准备 5 条以上不同文案
- **剔除不可达目标**：程序会自动删除无法发送的目标，首次运行后目标列表会自动精简

## 联系

原项目：https://t.me/pysmart
