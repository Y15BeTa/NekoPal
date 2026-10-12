#!/usr/bin/env python
"""把一段文字推给正在运行的 NekoPal。

    echo "一整块文字" | python scripts/push.py
    python scripts/push.py --file 回答.md
    python scripts/push.py "短文本" --voice edge:zh-CN-XiaoxiaoNeural
    python scripts/push.py --file 讲义.md --follow     # 顺便打印进度

任何程序（编辑器插件、脚本、定时任务）都可以用这个脚本或直接调用
``POST /api/read`` 把整块文本送进跟读窗。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def read_text(args: argparse.Namespace) -> str:
    if args.text:
        return args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as handle:
            return handle.read()
    return sys.stdin.read()


def follow(base: str, task_id: str) -> None:
    url = "{}/api/read/{}/events".format(base, task_id)
    with urllib.request.urlopen(url, timeout=600) as response:
        event_type = None
        for raw in response:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event: "):
                event_type = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
                if event_type == "plan":
                    print("  plan  -> {} 句".format(len(data["sentences"])))
                elif event_type == "segment":
                    print("  seg   -> i={} {:.2f}s words={}".format(
                        data["i"], data["duration"], len(data["words"])
                    ))
                elif event_type == "done":
                    print("  done  -> {:.2f}s".format(data.get("elapsed", 0)))
                    return
                elif event_type == "error":
                    print("  ERROR -> {}".format(data.get("message")))
                    return


def main() -> int:
    parser = argparse.ArgumentParser(description="把整块文字推给 NekoPal")
    parser.add_argument("text", nargs="?", help="要朗读的文字（省略则读 stdin 或 --file）")
    parser.add_argument("--file", help="从文件读取")
    parser.add_argument("--base", default="http://127.0.0.1:8787", help="NekoPal 服务地址")
    parser.add_argument("--voice", default=None, help="音色 ID（默认用服务端设置）")
    parser.add_argument("--start-index", type=int, default=0, help="从第几句开始读")
    parser.add_argument("--follow", action="store_true", help="顺便打印合成进度")
    args = parser.parse_args()

    text = read_text(args)
    if not text.strip():
        print("没有可朗读的文字", file=sys.stderr)
        return 2

    payload: dict = {
        "text": text,
        "source": {"kind": "block", "app": "push.py"},
        "options": {"start_index": args.start_index},
    }
    if args.voice:
        payload["voice"] = args.voice

    try:
        result = post_json(args.base + "/api/read", payload)
    except urllib.error.URLError as exc:
        print("连不上 NekoPal（{}）：{}".format(args.base, exc), file=sys.stderr)
        print("先跑 `uv run nekopal serve` 启动服务。", file=sys.stderr)
        return 1

    print("任务 {} · {} 字 · {} 句".format(
        result["task_id"], len(text), result["sentences"]
    ))
    if args.follow:
        follow(args.base, result["task_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
