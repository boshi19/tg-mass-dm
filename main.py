# main.py - WebUI 启动入口 (v4.0-web)
# 启动 FastAPI + uvicorn 服务，浏览器访问 http://127.0.0.1:8000

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="tg-mass-dm WebUI 启动器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    print("=" * 55)
    print("  tg-mass-dm WebUI v4.0-web")
    print("=" * 55)
    print(f"  访问地址: http://{args.host}:{args.port}")
    print(f"  按 Ctrl+C 停止服务\n")

    try:
        import uvicorn
    except ImportError:
        print("[错误] 未安装 uvicorn，请先执行：pip install -r requirements.txt")
        sys.exit(1)

    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
