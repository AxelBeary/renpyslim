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
from collections.abc import Iterable
from pathlib import Path

from .models import (
    AssetKind, SCRIPT_EXTS, SKIP_DIRS, ChangeRecord, kind_of,
)
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

    asset_names 是相对 game/ 的完整资产清单；流水线传入它可覆盖散文件与
    RPA 封包资源。省略时直接统计 game/ 下真实资产，过滤脚本与 SKIP_DIRS。
    裸文件名只有在对应资产唯一时才构成引用证据。
    """

    def __init__(self, game_dir: str,
                 asset_names: Iterable[str] | None = None):
        self.game_dir = Path(game_dir)
        self.files: dict[str, list[str]] = {}      # rel -> 行列表
        self.encodings: dict[str, str] = {}        # rel -> 写回用的编码
        self._asset_names: set[str] = set()
        self._load()
        if asset_names is not None:
            provided: set[str] = set()
            for name in asset_names:
                rel = self._normalize(name)
                if self._is_asset_name(rel):
                    provided.add(rel)
            self._asset_names = provided
        self._basename_counts = self._count_basenames(self._asset_names)

    @staticmethod
    def _normalize(name: str) -> str:
        return str(name).replace("\\", "/")

    @staticmethod
    def _is_asset_name(rel: str) -> bool:
        """只统计可优化资产；脚本与缓存/存档等跳过目录不参与歧义计数。"""
        if not rel or any(part in SKIP_DIRS for part in rel.split("/")):
            return False
        ext = Path(rel).suffix.lower()
        return ext not in SCRIPT_EXTS and kind_of(ext) != AssetKind.OTHER

    @staticmethod
    def _count_basenames(names: Iterable[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rel in names:
            bare = rel.rsplit("/", 1)[-1]
            counts[bare] = counts.get(bare, 0) + 1
        return counts

    def _load(self) -> None:
        for p in self.game_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(self.game_dir).as_posix()
            if any(part in SKIP_DIRS for part in p.relative_to(self.game_dir).parts):
                continue
            if self._is_asset_name(rel):
                self._asset_names.add(rel)
            if p.suffix.lower() not in SCRIPT_EXTS:
                continue
            # 审核修复：本工具注入的重映射脚本内含大量旧文件名键，
            # 进 RefIndex 会让"无引用判定/引用改写"全部误判，必须排除
            if p.name == REMAP_SCRIPT_NAME:
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
            self.files[rel] = text.splitlines(keepends=True)
            self.encodings[rel] = enc

    def _variants(self, name: str,
                  basename_counts: dict[str, int] | None = None) -> list[str]:
        """完整路径始终有效；裸名仅在该 basename 对应唯一资产时追加。"""
        name = self._normalize(name)
        v = [name]
        bare = name.rsplit("/", 1)[-1]
        counts = self._basename_counts if basename_counts is None else basename_counts
        if bare != name and counts.get(bare, 0) == 1:
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

    def refs_in_font_tags(self, rel_name: str) -> tuple[int, int]:
        """统计字体引用：(出现在闭合 {font=…}…{/font} 标签内的行数, 总引用行数)。

        与 find() 共用同一匹配模式、同行级口径。三重保守：
        同一行既有标签又有其他引用时该行不计入标签数；注释行（#
        开头）不参与统计——注释里的标签对判定是假阳性；标签必须在
        同一行闭合，未闭合标签（作用到行尾/跨行）拿不到归属证据。
        精确瘦身只在引用全都落在闭合标签里时启用，宁可回退全量。
        """
        total = tagged = 0
        for variant in self._variants(rel_name):
            ref_pat = re.compile(_LEFT_GUARD + re.escape(variant)
                                 + _RIGHT_GUARD)
            tag_pat = re.compile(r"\{font\s*=\s*[\"']?" + re.escape(variant)
                                 + r"[\"']?\s*\}.*\{/font\}")
            for lines in self.files.values():
                for line in lines:
                    if line.lstrip().startswith("#"):
                        continue   # 注释行：引用与标签都不算数（防假阳性）
                    if ref_pat.search(line):
                        total += 1
                        m = tag_pat.search(line)
                        if m:
                            # 同行混合引用防护：剔除闭合标签段后仍有引用，
                            # 说明该字体另有样式等用法（可能渲染任意文本），
                            # 该行不算“全在标签内”，宁可回退全量。
                            if not ref_pat.search(line[:m.start()]
                                                  + line[m.end():]):
                                tagged += 1
        return tagged, total

    def rewrite(self, mapping: dict[str, str]) -> list[ChangeRecord]:
        """批量改写引用。mapping: 旧相对路径 -> 新相对路径。

        返回修改记录（每个脚本文件一条，含改动数；逐行明细在 detail 里）。
        """
        records: list[ChangeRecord] = []
        normalized = {self._normalize(old): self._normalize(new)
                      for old, new in mapping.items()}
        # 长路径优先，避免短名先替换掉长路径的一部分
        pairs = sorted(normalized.items(), key=lambda kv: -len(kv[0]))
        # 目标裸名按“旧资产移除、新资产加入”后的清单判定，避免把唯一
        # 旧裸名改成一个仍有歧义的新裸名。
        projected = (self._asset_names - normalized.keys()) | set(normalized.values())
        target_counts = self._count_basenames(projected)
        patterns: list[tuple[str, str, re.Pattern]] = []
        for old, new in pairs:
            old_variants = self._variants(old)
            new_variants = self._variants(new, target_counts)
            for i, variant_old in enumerate(old_variants):
                # 新路径是根文件时只有完整路径；不能因变体数量不同而
                # 丢掉合法旧裸名的改写。
                variant_new = (new_variants[i] if i < len(new_variants)
                               else new_variants[0])
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
