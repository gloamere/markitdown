import argparse
import getpass
import os
import sys
from pathlib import Path

import uvicorn

from .state import Database, Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="邀请制文档转 Markdown 工作台（本机开发模式）")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("serve", "bootstrap-admin", "reset-password"),
        default="serve",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, help="私有数据目录，默认当前目录下 .markitdown-data")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须为 1–65535")
    os.umask(0o077)
    settings = Settings(data_dir=args.data_dir) if args.data_dir else Settings()
    if args.command in {"bootstrap-admin", "reset-password"}:
        # Never accept a password in argv, an environment variable, or a default.
        # The person operating the server must explicitly choose it in their terminal.
        if not sys.stdin.isatty():
            parser.error("请在交互式终端运行账号操作；密码不会从命令行参数读取")
        from .auth import AuthError, AuthService

        service = AuthService(Database(settings), settings)
        if args.command == "bootstrap-admin" and service.has_admin():
            parser.error("管理员已存在，不能重复初始化")
        username = input("账号用户名（3–32 位英文、数字、_ . -）：").strip()
        password = getpass.getpass("密码（6–128 个字符，建议使用长口令）：")
        confirmation = getpass.getpass("再次输入密码：")
        if password != confirmation:
            parser.error("两次密码不一致，未创建账户")
        try:
            if args.command == "bootstrap-admin":
                service.bootstrap_admin(username, password)
            else:
                service.reset_password(username, password)
        except AuthError as exc:
            parser.error(exc.detail)
        print(
            "管理员已初始化。启动工作台后登录，再创建邀请。"
            if args.command == "bootstrap-admin"
            else "密码已更新，原有会话已撤销；账号启停状态不变。"
        )
        return

    from .app import create_app

    # Still no public bind option: public deployment needs a separate security review.
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        proxy_headers=False,
        limit_concurrency=8,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=60,
    )


if __name__ == "__main__":
    main()
