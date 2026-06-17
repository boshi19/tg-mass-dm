# build.py - 用于一键打包 WebUI 为单文件 EXE 的自动化脚本
import os
import sys
import shutil
import subprocess
from pathlib import Path

def build_project():
    current_dir = Path(__file__).resolve().parent
    main_py = current_dir / "main.py"
    static_dir = current_dir / "static"
    
    if not main_py.exists():
        print(f"[错误] 未能找到启动入口 main.py，请确保 build.py 放在项目根目录。")
        return

    print("=======================================================")
    print(" 开始执行 tg-mass-dm WebUI 自动化打包流 (PyInstaller) ")
    print("=======================================================")

    # 1. 自动寻找当前 Python 环境中的 zoneinfo / tzdata 路径
    import zoneinfo
    tzdata_path = Path(zoneinfo.__file__).parent
    print(f"[探测] zoneinfo 数据路径: {tzdata_path}")

    # 2. 构建 PyInstaller 打包基础命令
    # --onefile 打包为单个exe; --clean 清理缓存
    cmd = [
        "pyinstaller",
        "--clean",
        "--onefile",
        f"--name=tg-mass-dm",
        f"--workpath={str(current_dir / 'build')}",
        f"--distpath={str(current_dir / 'dist')}",
        f"--specpath={str(current_dir)}",
    ]

    # 3. 显式收集前端静态资源目录 static
    if static_dir.exists():
        print(f"[收集] 静态资源目录: {static_dir} -> static/")
        cmd.append(f"--add-data={str(static_dir)}{os.pathsep}static")
    else:
        print(f"[警告] 未在当前目录下发现 static 文件夹！前端页面可能缺失。")

    # 4. 显式收集时区数据 tzdata 防止 Windows 运行时调度器报错
    if tzdata_path.exists():
        cmd.append(f"--add-data={str(tzdata_path)}{os.pathsep}zoneinfo")

    # 5. 显式追加 FastAPI, Uvicorn 以及 Telethon 核心依赖隐藏导入
    hidden_imports = [
        "app",  # main.py 中 uvicorn.run("app:app") 为字符串引用，需手动隐藏导入
        "config",
        "messages",
        "targets",
        "event_bus",
        "scheduler",
        "sender",
        "task_manager",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.lifespan.on",
        "fastapi.staticfiles",
    ]
    for imp in hidden_imports:
        cmd.append(f"--hidden-import={imp}")

    # 6. 追加目标启动主文件
    cmd.append(str(main_py))

    # 7. 调用 PyInstaller 进程执行打包
    print(f"[执行] 打包命令生成中，开始调用 PyInstaller 构建进程...")
    try:
        subprocess.check_call(cmd)
        print("\n=======================================================")
        print(" 🎉 打包完成！最终打包文件生成在: dist/tg-mass-dm.exe")
        print(" 💡 运行提示：请将 config.yaml、usernames.txt、messages.txt")
        print("    以及 sessions 文件夹放置在与该 .exe 同级目录下即可正常运行。")
        print("=======================================================")
    except subprocess.CalledProcessError as e:
        print(f"\n[错误] 打包失败，PyInstaller 进程返回异常: {e}")
    except FileNotFoundError:
        print(f"\n[错误] 运行失败！未在当前环境中检测到 PyInstaller。请先执行: pip install pyinstaller")

if __name__ == "__main__":
    build_project()