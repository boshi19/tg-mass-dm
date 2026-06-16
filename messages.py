# messages.py - 文案池 + 随机尾部

import random
import uuid
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
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"[错误] 文案池文件不存在: {path.resolve()}")

    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if not lines:
        raise ValueError("[错误] 文案池为空，请在 messages.txt 中添加至少一条文案")

    print(f"[信息] 已加载 {len(lines)} 条文案模板")
    return lines


def append_random_tail(text: str, style: str = "dots") -> str:
    """
    在消息末尾追加随机标识，确保每条消息的 Hash 值不同。

    style="dots": 追加 1-5 个随机位置的 "." 字符
    style="ref" : 追加 [ref:a3f8b2c] 格式的随机 ID
    """
    if style == "ref":
        short_id = uuid.uuid4().hex[:7]
        return f"{text} [ref:{short_id}]"

    # 默认 dots 模式，在随机位置插入 "."
    dot_count = random.randint(1, 5)
    dots = "." * dot_count
    if len(text) > 3 and random.random() < 0.5:
        pos = random.randint(len(text) // 2, len(text) - 1)
        return text[:pos] + dots + text[pos:]
    return text + dots
