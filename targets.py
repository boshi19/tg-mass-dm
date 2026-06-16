# targets.py - 目标用户列表管理

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
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"[错误] 目标列表文件不存在: {path.resolve()}")

    raw = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    # 去重并保持顺序
    seen = set()
    targets = []
    for t in raw:
        if t not in seen:
            seen.add(t)
            targets.append(t)

    if not targets:
        raise ValueError("[错误] 目标列表为空，请在 usernames.txt 中添加目标用户")

    print(f"[信息] 已加载 {len(targets)} 个目标用户")
    return targets


def remove_target(file_path: str | Path, username: str) -> bool:
    """
    从目标列表文件中移除指定用户名（原地修改）。

    参数:
        file_path: str | Path
        username: str - 要移除的用户名
    返回:
        bool - True=已移除, False=未找到或文件不存在
    """
    path = Path(file_path)
    if not path.exists():
        return False
    file_lines = path.read_text(encoding="utf-8").splitlines()
    new_lines = []
    removed = False
    for line in file_lines:
        if line.strip() == username:
            removed = True
            continue
        new_lines.append(line)
    if removed:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return removed
