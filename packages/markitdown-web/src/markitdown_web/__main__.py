import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="本机文档转 Markdown 工作台")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须为 1–65535")
    # Deliberately no --host option: this MVP has no public-service auth layer.
    uvicorn.run(
        "markitdown_web.app:app",
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        limit_concurrency=8,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
