import argparse
import getpass
import json
import os
import sys
from pathlib import Path

import uvicorn

from .state import Database, Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="邀请制文档转 Markdown 工作台：单实例服务与离线维护")
    parser.add_argument(
        "command",
        nargs="?",
        choices=(
            "serve",
            "bootstrap-admin",
            "reset-password",
            "backup",
            "export-recovery-journal",
            "restore",
            "preflight",
        ),
        default="serve",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, help="私有数据目录，默认当前目录下 .markitdown-data")
    parser.add_argument("--backup-dir", type=Path, help="新的私有备份目录，或恢复时的备份来源")
    parser.add_argument("--journal-file", type=Path, help="独立保存的最新恢复日志文件")
    parser.add_argument("--target-dir", type=Path, help="恢复目标：新的空目录，不覆盖当前数据")
    parser.add_argument(
        "--confirm-latest-journal",
        action="store_true",
        help="操作者确认日志覆盖备份后的全部删除、停用和密码恢复事件",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须为 1–65535")
    os.umask(0o077)
    settings = Settings(data_dir=args.data_dir) if args.data_dir else Settings()
    if args.command in {"backup", "export-recovery-journal", "restore"}:
        from .recovery import (
            RecoveryError,
            create_backup,
            export_recovery_journal,
            restore_backup,
        )

        try:
            if args.command == "backup":
                if args.backup_dir is None:
                    parser.error("backup 需要 --backup-dir；服务必须已停止")
                result = create_backup(settings.data_dir, args.backup_dir)
            elif args.command == "export-recovery-journal":
                if args.journal_file is None:
                    parser.error("export-recovery-journal 需要 --journal-file；服务必须已停止")
                result = export_recovery_journal(settings.data_dir, args.journal_file)
            else:
                if (
                    args.backup_dir is None
                    or args.target_dir is None
                    or args.journal_file is None
                ):
                    parser.error(
                        "restore 需要 --backup-dir、--target-dir 和 --journal-file"
                    )
                if not args.confirm_latest_journal:
                    parser.error(
                        "恢复前必须人工确认日志最新且完整，并传入 --confirm-latest-journal；缺失日志时不可恢复旧快照"
                    )
                result = restore_backup(
                    args.backup_dir,
                    args.target_dir,
                    args.journal_file,
                    confirm_latest_journal=True,
                )
        except RecoveryError as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.command == "preflight":
        from .engines import engine_config

        profiles = engine_config(settings)
        print(
            json.dumps(
                {"deployment_mode": settings.deployment_mode, "profiles": profiles},
                ensure_ascii=False,
            )
        )
        if settings.deployment_mode != "production":
            print("本地开发模式不构成生产隔离验收。", file=sys.stderr)
        if not profiles[0]["available"] or (
            settings.docling_enabled and not profiles[1]["available"]
        ):
            raise SystemExit(1)
        return
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
