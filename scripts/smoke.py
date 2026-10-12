#!/usr/bin/env python
"""端到端冒烟测试：POST /api/read → 消费 SSE → 取回音频。

用法：
    python scripts/smoke.py [--base http://127.0.0.1:8787] [--voice say:auto]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

SAMPLE = """## 一段用来试读的文字

这是**第一段**。这里用 `attention` 这个术语，看看中英混排读得对不对。

```python
print("这段代码应该被跳过")
```

| 表格 | 列 |
| --- | --- |
| 甲 | 乙 |

第二句：A → B，且 x ≥ 3。第三句用来验证分句是否正常。
"""


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8787")
    parser.add_argument("--voice", default="say:auto")
    parser.add_argument("--text", default=None)
    args = parser.parse_args()

    text = args.text or SAMPLE
    started = post_json(
        args.base + "/api/read",
        {"text": text, "voice": args.voice, "source": {"kind": "block", "app": "smoke"}},
    )
    print("POST /api/read ->", started)

    task_id = started["task_id"]
    t0 = time.time()
    first_segment_at = None
    segments = []

    url = "{}/api/read/{}/events".format(args.base, task_id)
    with urllib.request.urlopen(url, timeout=120) as response:
        event_type = None
        for raw in response:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event: "):
                event_type = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
                if event_type == "plan":
                    print("plan  -> {} 句".format(len(data["sentences"])))
                    for sentence in data["sentences"]:
                        print("       [{}] {}".format(sentence["i"], sentence["text"]))
                elif event_type == "segment":
                    segments.append(data)
                    if first_segment_at is None:
                        first_segment_at = time.time() - t0
                    print(
                        "seg   -> i={} dur={}s words={} {}".format(
                            data["i"], data["duration"], len(data["words"]), data["audio"]
                        )
                    )
                elif event_type == "done":
                    print("done  ->", data)
                    break
                elif event_type == "error":
                    print("ERROR ->", data)
                    break

    if first_segment_at is not None:
        print("首段延迟: {:.2f}s".format(first_segment_at))

    if not segments:
        print("没有拿到任何音频段")
        return 1

    with urllib.request.urlopen(args.base + segments[0]["audio"], timeout=60) as response:
        blob = response.read()
        print(
            "音频    -> {} 字节, 头部={!r}, Content-Type={}".format(
                len(blob), blob[:4], response.headers.get("Content-Type")
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
