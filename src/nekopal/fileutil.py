"""原子写文件（**写坏了要能看出来** ✗ 而不是留个半截文件 ✗）。

## 为什么有它

2026-10-17 踩到一次**静默数据丢失** ✗：

`lines.yaml`（台词池 ✗ 用户的手工改动都在里面 ✗）里 `lines` 那一行**丢了一个冒号** ✗ ——

```yaml
    fingerprint: f40ff1ce5cf0
    lines            ← 冒号没了 ✗
    - 今天也ぽかぽか
```

YAML 于是解析失败 ✗ 而调用方（`load_lines`）**把异常吞掉当成"池子是空的"** ✗
→ 界面上表现成"**池子凭空消失了**" ✗ 而不是"文件坏了" ✗。

**根因**：`_write_pool_file()` 用的是 `path.write_text()` ✗ ——
它是 **open(截断) → write** ✗ 中间没有任何保护 ✗。
只要写入过程中被打断 ✗ 或者**两个写者撞上** ✗ 就会留下**半截文件** ✗。

**修法**：先写同目录下的临时文件 ✗ 再 `os.replace()` 换过去 ✗ ——
`os.replace` 在同一个文件系统上是**原子**的 ✗ ✅ 所以读者要么看到旧的、要么看到新的 ✗
永远不会看到写了一半的 ✗。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, text: str, mode: int | None = None) -> None:
    """原子地把 ``text`` 写进 ``path``（同目录临时文件 + `os.replace`）。

    ``mode`` 给了就在换过去之后设权限（如 `0o600` ✗ —— 这些文件里有私人语境 ✗）。

    ⚠️ 临时文件**必须和目标同目录** ✗ —— `os.replace` 只在**同一个文件系统**内原子 ✗
    （放 `/tmp` 再换过去可能是跨设备 ✗ 那时它会退化成"复制 + 删除" ✗ 又不原子了 ✗）。
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix="." + path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            # 顺序很重要：先把内容刷到盘上，再换名字 ✗
            # （不然断电时可能出现"名字是新的、内容是空的"✗）
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    if mode is not None:
        try:
            path.chmod(mode)
        except OSError:
            pass
