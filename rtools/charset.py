"""字符集提取：扫描项目文本文件，收集实际使用的字符。

四层防线中的第 1 层（全项目扫描）。保底字符集由 config.CharsetOptions
提供（第 2 层），手动追加由 extra_chars 提供（第 3 层）。
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import SKIP_DIRS, TEXT_EXTS
from .config import CharsetOptions

# 检测"玩家可输入任意文字"的写法，出现则字体瘦身有风险
DYNAMIC_INPUT_RE = re.compile(r"\brenpy\.input\s*\(|(?<![\w.])input\s*\(")


def read_text_robust(path: Path) -> str:
    """稳健读文本：先按 UTF-8，失败再试 GB18030，都不行再宽容解码。

    历史教训：老工具用错编码读文件，汉字变乱码，字符集对不上字体，
    瘦身出空壳。这里从源头堆死编码问题。
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def read_rpyc_text(path: Path) -> str:
    """读取 .rpyc/.rpymc 里的文本内容。

    格式（与官方 renpy/script.py 的 read_rpyc_data 对齐）：
    - 旧格式：整个文件是 zlib 压缩的 pickle；
    - 新格式（RPYC2）：首 10 字节 "RENPY RPC2" + 3 个 12 字节的
      槽位表（槽号/偏移/长度），槽内是 zlib 压缩的 pickle。
    这里只做解压收集字符，不反序列化 pickle，无安全风险。
    """
    import struct
    import zlib
    try:
        raw = path.read_bytes()
    except OSError:
        return ""

    chunks: list[bytes] = []
    if raw[:10] == b"RENPY RPC2":
        pos = 10
        while pos + 12 <= len(raw):
            slot, start, length = struct.unpack("III", raw[pos:pos + 12])
            if slot == 0:
                break
            try:
                chunks.append(zlib.decompress(raw[start:start + length]))
            except Exception:
                pass
            pos += 12
    else:
        try:
            chunks.append(zlib.decompress(raw))
        except Exception:
            return ""

    text = "".join(c.decode("utf-8", errors="ignore") for c in chunks)
    return text


def _iter_text_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        # 审核修复：编译脚本 .rpyc/.rpymc 里的文本也参与字符集统计，
        # 补上"工程里只有编译产物、或 rpy 与 rpyc 内容不一致"的盲区
        if p.suffix.lower() in TEXT_EXTS or p.suffix.lower() in (".rpyc", ".rpymc"):
            yield p


def extract_charset(root: str, options: CharsetOptions
                    ) -> tuple[set[str], list[str]]:
    """扫描 root 下所有文本文件，返回 (字符集合, 全局警告列表)。

    工程模式下 root 通常指向 game/ 目录（也兼容指向工程根）。
    """
    root_p = Path(root)
    chars: set[str] = set()
    warnings: list[str] = []
    dynamic_input_files: list[str] = []

    for p in _iter_text_files(root_p):
        suffix = p.suffix.lower()
        if suffix in (".rpyc", ".rpymc"):
            text = read_rpyc_text(p)
            # 编译产物含 pickle 操作码等不可打印字节，只收可打印字符，
            # 与成品模式口径保持一致，不给字体瘦身引入垃圾字形需求
            chars.update(c for c in text if c.isprintable())
            continue
        text = read_text_robust(p)
        if not text:
            continue
        chars.update(text)
        if suffix in (".rpy", ".rpym", ".py"):
            if DYNAMIC_INPUT_RE.search(text):
                dynamic_input_files.append(p.relative_to(root_p).as_posix())

    # 保底字符集（第 2 层）+ 手动追加（第 3 层）
    chars.update(options.base_text())
    chars.discard("\x00")

    if dynamic_input_files:
        files_show = "、".join(dynamic_input_files[:5])
        more = f" 等 {len(dynamic_input_files)} 个文件" if len(dynamic_input_files) > 5 else ""
        warnings.append(
            f"检测到玩家输入文本的代码（{files_show}{more}）：玩家可能打出任意字符，"
            "字体瘦身后这些输入可能显示为方框。建议在下方\"手动追加字符\"里补上常用字，"
            "或勾选更多保底字符集。"
        )
    return chars, warnings


def extract_charset_dist(root: str, options: CharsetOptions
                         ) -> tuple[set[str], list[str]]:
    """成品模式的字符集提取：脚本是编译后的 .rpyc，无法按文本解析。

    采用解压扫描法：.rpyc 是 zlib 压缩的 pickle，先解压再按
    utf-8 宽容解码收集可打印字符；pickle 操作码均为 ASCII，
    会被保底拉丁集覆盖，不会引入多余汉字。不反序列化 pickle，无安全风险。
    """
    root_p = Path(root)
    chars: set[str] = set()

    for p in root_p.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root_p).parts):
            continue
        suffix = p.suffix.lower()
        if suffix in (".rpyc", ".rpymc"):
            text = read_rpyc_text(p)
        elif suffix in TEXT_EXTS:
            # 审核修复：旧写法 decode("utf-8", errors="ignore") 会把
            # GBK/GB18030 编码的文本文件里的汉字整片丢掉，改用带编码
            # 回退的稳健读取（utf-8 -> gb18030）
            text = read_text_robust(p)
        else:
            continue
        chars.update(c for c in text if c.isprintable())

    chars.update(options.base_text())
    chars.discard("\x00")
    warnings = [
        "成品模式下无法识别动态输入代码，字符集仅来自文件内容扫描，"
        "若游戏存在玩家打字输入，建议勾选更多保底字符集或手动追加常用字。"
    ]
    return chars, warnings


def extract_charset_sources(paths: list[str], options: CharsetOptions
                            ) -> tuple[set[str], list[str]]:
    """从任意多个文本来源（文件或目录混合）提取字符集。

    供独立字体瘦身用：目录按 TEXT_EXTS 递归扫描，文件直接读。
    """
    chars: set[str] = set()
    warnings: list[str] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            got, warns = extract_charset(str(p), options)
            chars |= got
            # 目录级提取已含保底集与动态输入警告，这里只取警告避免重复加保底
            warnings.extend(warns)
            # 保底集由最后统一加，先去掉目录提取带进来的（无妨，下方会重新并）
        elif p.is_file():
            text = read_text_robust(p)
            if not text:
                warnings.append(f"读不了这个文件，已跳过：{p.name}")
                continue
            chars.update(text)
        else:
            warnings.append(f"路径不存在，已跳过：{raw}")
    chars.update(options.base_text())
    chars.discard("\x00")
    return chars, warnings


def find_missing_glyphs(font_path: str, chars: set[str], limit: int = 50
                        ) -> list[str]:
    """缺字对账：返回字符集里用到、但该字体没有的字（最多 limit 个）。

    用于字体瘦身前的安全提醒：这些字无论瘦不瘦身都会显示方框，
    除非游戏配置了回退字体。读取失败返回空列表（不阻断流程）。
    """
    from fontTools.ttLib import TTFont
    font = None
    try:
        font = TTFont(font_path, fontNumber=0, lazy=True)
        cmap = font.getBestCmap() or {}
        missing = sorted(c for c in chars if c.isprintable() and ord(c) not in cmap)
        return missing[:limit]
    except Exception:
        return []
    finally:
        # 审核修复（中-24）：getBestCmap 抛异常时已打开的句柄也要关
        if font is not None:
            try:
                font.close()
            except Exception:
                pass


def preview_subset(chars: set[str], font_path: str) -> dict:
    """瘦身前预览（第 4 层）：统计字体原有字形数与保留后字形数。"""
    from fontTools.ttLib import TTFont
    font = None
    try:
        font = TTFont(font_path, fontNumber=0, lazy=True)
        cmap = font.getBestCmap() or {}
        total_glyphs = len(cmap)
        keep = sum(1 for c in chars if ord(c) in cmap)
        return {
            "total_glyphs": total_glyphs,
            "keep_glyphs": keep,
            "drop_glyphs": total_glyphs - keep,
            "charset_size": len(chars),
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        # 审核修复（中-24）：同上，异常路径也要关句柄
        if font is not None:
            try:
                font.close()
            except Exception:
                pass
