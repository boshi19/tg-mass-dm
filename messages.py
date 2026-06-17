# messages.py - 文案池 + 随机尾部

import datetime as dt
import random
import re
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


RISK_TERMS = [
    "免费赚钱",
    "稳赚",
    "博彩",
    "贷款",
    "返利",
    "投资群",
    "加微信",
    "加QQ",
    "点击链接",
    "http://",
    "https://",
]


def render_template(template: str, context: dict | None = None) -> str:
    """渲染安全占位符；未知占位符保留原样，避免误删文案。"""
    context = context or {}
    values = {
        "username": context.get("username", ""),
        "target": context.get("target", context.get("username", "")),
        "account": context.get("account", ""),
        "date": dt.date.today().isoformat(),
    }

    def replace(match: re.Match) -> str:
        key = match.group(1)
        return str(values.get(key, match.group(0)))

    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", replace, template)


def apply_light_variation(text: str) -> str:
    """做轻量自然变体：问候、结尾和换行变化，不改变核心含义。"""
    prefixes = ["", "你好，", "您好，"]
    suffixes = ["", "谢谢。", "方便的话可以回复我。"]
    result = text.strip()
    if result and random.random() < 0.25 and not result.startswith(("你", "您", "Hi", "Hello")):
        result = random.choice(prefixes) + result
    if result and random.random() < 0.25:
        result = result.rstrip("。.!！") + random.choice(["。", "！", "."])
    if result and random.random() < 0.2:
        result = result + "\n" + random.choice(suffixes)
    return result.strip()


def compose_message(
    template: str,
    context: dict | None = None,
    tail_enabled: bool = True,
    tail_style: str = "dots",
    variation_enabled: bool = True,
) -> str:
    text = render_template(template, context)
    if variation_enabled:
        text = apply_light_variation(text)
    if tail_enabled:
        text = append_random_tail(text, tail_style)
    return text


def detect_risky_terms(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    for term in RISK_TERMS:
        if term.lower() in lowered and term not in found:
            found.append(term)
    return found
