"""从 **GPT-SoVITS 交付包**生成 NekoPal 音色包。

交付包的形态（社区常见）::

    <交付包>/
      模型索引.json          ← 契约
      <角色>/
        模型/  <角色>_GPT_e8.ckpt + <角色>_SoVITS_e8.pth
        推荐参考/ <语气组>/01_….wav + .txt   + 参考索引.json
        原素材/  …（训练素材，**推理用不到，本工具会跳过**）

本工具做的事：给每个角色在 `~/.config/nekopal/voices/` 下生成一个音色包 ——
复制一段参考音频进去、把两个权重挂上、写好 `voice.yaml`。之后它就会
**自动出现在界面的音色下拉列表里**。

用法::

    # 先看看会生成什么（不写任何文件）
    python integrations/sovits/import_pack.py ~/Downloads/<你的交付包> --dry-run

    # 真的生成（权重默认用**软链接**，不占额外空间）
    python integrations/sovits/import_pack.py ~/Downloads/<你的交付包>

    # 只导其中两个角色
    python integrations/sovits/import_pack.py <包> --only ritsu,yuno

    # 想把权重**拷进**音色包（更稳，但每个角色多占 ~315 MB）
    python integrations/sovits/import_pack.py <包> --copy-weights

⚠️ 授权：交付包**不会**说明授权状况，而这类社区模型基本是"未获权利方授权"。
本工具一律写成 `license: unofficial` + `commercial_ok: false` + `redistributable: false`
—— 要改是你自己的判断，别让工具替你声明。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 交付包里的这些目录是**训练素材**，推理用不到（往往比模型还大）
SKIP_DIRS = ("原素材",)


def _slug(text: str) -> str:
    """把模型 key 收拾成能当目录名/ID 用的形式。"""

    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return cleaned or "voice"


def _one_line(text: str) -> str:
    """参考文本里可能有换行（多句拼的）—— GPT-SoVITS 的 prompt_text 要一行。"""

    return re.sub(r"\s+", " ", str(text or "")).strip()


def _quoted(value: str) -> str:
    return '"{}"'.format(str(value).replace("\\", "\\\\").replace('"', '\\"'))


def load_index(package: Path) -> Dict[str, Any]:
    index_path = package / "模型索引.json"
    if not index_path.is_file():
        raise SystemExit("在 {} 里没找到 模型索引.json —— 这看着不像交付包".format(package))
    return json.loads(index_path.read_text(encoding="utf-8"))


def pick_reference(bank: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """挑一段参考音频：优先用交付包自己标的那组/那条，挑不到就退回第一条。"""

    groups = bank.get("emotion_groups") or {}
    if not groups:
        return None
    key = bank.get("default_emotion") or next(iter(groups))
    group = groups.get(key) or next(iter(groups.values()))
    label = str(group.get("label") or key)
    refs = group.get("references") or []
    if not refs:
        return None
    want = bank.get("default_reference_choice", 1)
    for entry in refs:
        if entry.get("choice") == want:
            return dict(entry, __emotion=label)
    return dict(refs[0], __emotion=label)


def build_pack(
    package: Path,
    voices_dir: Path,
    key: str,
    model: Dict[str, Any],
    *,
    copy_weights: bool,
    dry_run: bool,
) -> List[str]:
    """给一个角色生成音色包，返回给人看的日志行。"""

    log: List[str] = []
    name = str(model.get("name") or key)
    pack_id = "yumemita-{}".format(_slug(key))
    target = voices_dir / pack_id

    bank_path = package / str(model["reference_index"])
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    reference = pick_reference(bank)
    if reference is None:
        return ["  ✗ {}：参考索引里一条参考都没有，跳过".format(name)]

    ref_src = package / str(reference["audio"])
    gpt_src = package / str(model["gpt"])
    sovits_src = package / str(model["sovits"])
    for path, label in ((ref_src, "参考音频"), (gpt_src, "GPT 权重"), (sovits_src, "SoVITS 权重")):
        if not path.is_file():
            return ["  ✗ {}：{}不存在（{}）".format(name, label, path)]

    prompt_text = _one_line(reference.get("text") or "")
    prompt_lang = str(bank.get("prompt_lang") or "all_ja")
    emotion = str(reference.get("__emotion") or reference.get("emotion") or "")

    log.append("  {}  →  {}".format(name, pack_id))
    log.append("     参考：{}  「{}」".format(ref_src.name[:46], prompt_text[:46]))

    if dry_run:
        return log

    target.mkdir(parents=True, exist_ok=True)

    # ① 参考音频**拷进去**（几百 KB，让包至少"参考"这一半是自足的）
    shutil.copy2(ref_src, target / "ref.wav")

    # ② 两个权重：默认**软链接**（每个角色 ~315 MB，没必要复制一份）
    (target / "模型").mkdir(exist_ok=True)
    for src in (gpt_src, sovits_src):
        dest = target / "模型" / src.name
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        if copy_weights:
            shutil.copy2(src, dest)
        else:
            dest.symlink_to(src)
    log.append(
        "     权重：{}".format("已拷贝" if copy_weights else "软链接 → 交付包")
    )

    # ③ voice.yaml
    target.joinpath("voice.yaml").write_text(
        _voice_yaml(
            pack_id=pack_id,
            name=name,
            gpt_name=gpt_src.name,
            sovits_name=sovits_src.name,
            prompt_text=prompt_text,
            prompt_lang=prompt_lang,
            emotion=emotion,
            linked=not copy_weights,
        ),
        encoding="utf-8",
    )
    return log


def _voice_yaml(
    *,
    pack_id: str,
    name: str,
    gpt_name: str,
    sovits_name: str,
    prompt_text: str,
    prompt_lang: str,
    emotion: str,
    linked: bool,
) -> str:
    weight_note = (
        "  # ⚠️ 这两个是**软链接**，指向你下载的交付包。\n"
        "  #    删掉/移动交付包，这个音色就会失效（重跑本工具即可）。\n"
        "  #    想让它自足：重跑本工具并加 --copy-weights\n"
        if linked
        else "  # 权重已**拷贝**进本目录，交付包删掉也不影响。\n"
    )
    return """\
# 由 integrations/sovits/import_pack.py 从 GPT-SoVITS 交付包生成。
#
# ⚠️ 授权：交付包没说授权状况，这里一律按最保守的写
#    （社区训练的二次元角色音色，基本属于"未获权利方授权"）。
#    要改是你自己的判断 —— 别让工具替你声明。
id: {pack_id}
name: {name}
backend: sovits
lang: ["ja"]
tags: ["community", "yumemita"]
license: unofficial
commercial_ok: false
redistributable: false
description: {description}

sovits:
  gpt_weights: 模型/{gpt_name}
  sovits_weights: 模型/{sovits_name}
{weight_note}  ref_audio: ref.wav
  # 参考音频的**逐字文本**：换掉 ref.wav 就要一起改这一行
  prompt_text: {prompt_text}
  # 参考音频是**日语** —— 这个别改（它决定"像不像"）
  prompt_lang: {prompt_lang}
  # ⚠️ 要合成的**文本**语言：`auto` = 自动识别 ✗ **不要**跟着 prompt_lang 走 ✗
  #    踩着过：写成 all_ja 时，NekoPal 里的**中文文本会被用日语发音读出来** ✗
  #    （同一句中文：all_ja 出来 80KB ✗ zh/auto 是 198KB ✗ 完全两样 ✗）
  #    GPT-SoVITS 支持**跨语言**：日语参考 + 中文文本 → 她的音色说中文 ✅
  text_lang: auto
""".format(
        pack_id=_quoted(pack_id),
        name=_quoted("夢限大·{}".format(name)),
        description=_quoted(
            "GPT-SoVITS · {}{}（语音克隆，不是变声）".format(
                name, " · 参考语气：" + emotion if emotion else ""
            )
        ),
        gpt_name=gpt_name,
        sovits_name=sovits_name,
        weight_note=weight_note,
        prompt_text=_quoted(prompt_text),
        prompt_lang=_quoted(prompt_lang),
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从 GPT-SoVITS 交付包生成 NekoPal 音色包")
    parser.add_argument("package", help="交付包目录（里面有 模型索引.json）")
    parser.add_argument("--voices-dir", default=str(Path.home() / ".config/nekopal/voices"))
    parser.add_argument("--only", default="", help="只导这些模型 key，逗号分隔（如 ritsu,yuno）")
    parser.add_argument("--copy-weights", action="store_true", help="把权重拷进包（默认软链接）")
    parser.add_argument("--dry-run", action="store_true", help="只看会生成什么，不写文件")
    args = parser.parse_args(argv)

    package = Path(args.package).expanduser().resolve()
    voices_dir = Path(args.voices_dir).expanduser()
    index = load_index(package)
    models: Dict[str, Any] = index.get("models") or {}
    if not models:
        raise SystemExit("模型索引.json 里没有 models 段")

    wanted = {x.strip() for x in args.only.split(",") if x.strip()}
    print("交付包：{}".format(package))
    print("版本  ：{}".format(index.get("version", "?")))
    print("音色放：{}".format(voices_dir))
    print("权重  ：{}".format("拷贝" if args.copy_weights else "软链接（省空间）"))
    print()

    made = 0
    for key, model in models.items():
        if wanted and key not in wanted:
            continue
        for line in build_pack(
            package,
            voices_dir,
            key,
            model,
            copy_weights=args.copy_weights,
            dry_run=args.dry_run,
        ):
            print(line)
        made += 1

    print()
    print("{} {} 个音色".format("会生成" if args.dry_run else "已生成", made))
    if not args.dry_run and made:
        print("重启 NekoPal（或点设置里的「刷新」）就能在下拉列表里看到它们。")
        print("⚠️ 还差一步：起 GPT-SoVITS 服务，否则选了会说\"连不上\"。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
