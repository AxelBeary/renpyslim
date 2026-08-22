"""引用检测与改写引擎（仅工程模式 A/C 使用）。

设计要点（对应需求基线第四节第 2 条）：
- 只有脚本里能找到字面引用的资源，才允许改名/换格式；
  找不到引用的资源视为"可能被目录自动加载或变量拼接引用"，
  只做原地压缩，绝不改名。
- 替换按"长路径优先"执行，避免短名误伤长路径。
- 每处替换都记录 文件+行号+旧->新，汇入修改清单。
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import SCRIPT_EXTS, SKIP_DIRS, ChangeRecord
from .remap import REMAP_SCRIPT_NAME

# 匹配点前面不允许是"文件名一部分"的字符，防止 logo.png 误匹配 gui_logo.png
_LEFT_GUARD = r"(?<![\w.\-/])"
# 审核修复（中-22）：右边界同样要守卫——旧版只有左守卫，
# bg.png 会把 bg.png.png 改成 bg.webp.png、把 bg.png@2x 改成
# bg.webp@2x（静默断链）；find 与 rewrite 共用同一模式
_RIGHT_GUARD = r"(?![\w.\-/@])"


class RefIndex:
    """把 game 目录下所有脚本文本读入内存，供查找与批量改写。

    编码安全：优先按 UTF-8 严格解码；失败则回退 latin-1（任意字节
    双射，保证写回时逐字节无损）。旧版用 errors="replace" 读取，
    遇到非 UTF-8 脚本写回时会永久损坏文件，已修复。
    """

    def __init__(self, game_dir: str):
        self.game_dir = Path(game_dir)
        self.files: dict[str, list[str]] = {}      # rel -> 行列表
        self.encodings: dict[str, str] = {}        # rel -> 写回用的编码
        self._load()

    def _load(self) -> None:
        for p in self.game_dir.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in SCRIPT_EXTS:
                continue
            # 审核修复：本工具注入的重映射脚本内含大量旧文件名键，
            # 进 RefIndex 会让"无引用判定/引用改写"全部误判，必须排除
            if p.name == REMAP_SCRIPT_NAME:
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(self.game_dir).parts):
                continue
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            try:
                text = raw.decode("utf-8")
                enc = "utf-8"
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
                enc = "latin-1"
            rel = p.relative_to(self.game_dir).as_posix()
            self.files[rel] = text.splitlines(keepends=True)
            self.encodings[rel] = enc

    def _variants(self, name: str) -> list[str]:
        """一个资源可能被引用的写法：完整相对路径、纯文件名。"""
        name = name.replace("\\", "/")
        v = [name]
        bare = name.rsplit("/", 1)[-1]
        if bare != name:
            v.append(bare)
        return v

    def find(self, rel_name: str) -> list[tuple[str, int]]:
        """返回引用位置列表 [(脚本相对路径, 行号)]，行号从 1 开始。"""
        hits: list[tuple[str, int]] = []
        for variant in self._variants(rel_name):
            pat = re.compile(_LEFT_GUARD + re.escape(variant) + _RIGHT_GUARD)
            for frel, lines in self.files.items():
                for i, line in enumerate(lines, start=1):
                    if pat.search(line):
                        hits.append((frel, i))
        return hits

    def rewrite(self, mapping: dict[str, str]) -> list[ChangeRecord]:
        """批量改写引用。mapping: 旧相对路径 -> 新相对路径。

        返回修改记录（每个脚本文件一条，含改动数；逐行明细在 detail 里）。
        """
        records: list[ChangeRecord] = []
        # 长路径优先，避免 "bg.png" 先替换掉 "images/bg.png" 的一部分
        pairs = sorted(mapping.items(), key=lambda kv: -len(kv[0]))
        patterns: list[tuple[str, str, re.Pattern]] = []
        for old, new in pairs:
            for variant_old, variant_new in zip(self._variants(old),
                                                self._variants(new)):
                pat = re.compile(_LEFT_GUARD + re.escape(variant_old)
                                 + _RIGHT_GUARD)
                patterns.append((variant_old, variant_new, pat))

        for frel, lines in self.files.items():
            # 审核修复：同 _load，注入脚本永不参与改写（双保险）
            if Path(frel).name == REMAP_SCRIPT_NAME:
                continue
            changed = 0
            line_details: list[str] = []
            new_lines = []
            for i, line in enumerate(lines, start=1):
                cur = line
                for old_v, new_v, pat in patterns:
                    cur, n = pat.subn(new_v, cur)
                    if n:
                        changed += n
                        line_details.append(f"L{i}: {old_v} -> {new_v} (x{n})")
                new_lines.append(cur)
            if changed:
                path = self.game_dir / frel
                enc = self.encodings.get(frel, "utf-8")
                path.write_bytes("".join(new_lines).encode(enc))
                records.append(ChangeRecord(
                    action="rename_ref",
                    src=frel,
                    detail=f"共替换 {changed} 处引用；" + "；".join(line_details[:20])
                           + ("；……" if len(line_details) > 20 else ""),
                    ref_file=frel,
                    ref_line=0,
                ))
        return records
