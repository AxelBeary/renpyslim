"""字体瘦身：基于字符集做子集化（fontTools subset）。"""
from __future__ import annotations

from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont


def _subset_options() -> subset.Options:
    options = subset.Options()
    options.layout_features = ["*"]      # 保留全部排版特性，兼容性优先
    options.name_IDs = ["*"]            # 保留字体名信息
    options.name_legacy = True
    options.name_languages = ["*"]
    options.notdef_outline = True       # 保留缺字方框字形，缺字时不至于空白
    options.recalc_bounds = True
    options.ignore_missing_glyphs = True  # 字符集里字体没有的字直接跳过，不报错
    return options


def _expected_keep(font, chars: set[str]) -> int:
    """字符集与字体字形的交集数：理论上瘦身至少该保留这么多个码位。"""
    cmap = font.getBestCmap() or {}
    return sum(1 for c in chars if ord(c) in cmap)


def _sanity_check(expected: int, keep_after: int) -> None:
    """空壳防御：应保留的字很多、实际却没剩几个，判定瘦身异常。

    历史教训：字符集提取出错（如编码错乱）时，瘦身会把字形删光，
    产出 1~2KB 的空壳字体。此处直接拒绝，宁可不动原字体。
    """
    if expected >= 20 and keep_after < expected * 0.5:
        raise ValueError(
            f"瘦身结果异常：应保留约 {expected} 个字形，实际只剩 {keep_after} 个，"
            "疑似字符集与字体对不上（编码或字体格式问题）。已拒绝该结果，"
            "原字体保持不变。")


def subset_font_object(font: TTFont, dst: str, chars: set[str]) -> dict:
    """对已打开的字体对象做子集化，输出到 dst。

    供单字体与 TTC 拆出的字重复用。安全策略同 subset_font：
    先写临时文件，确认变小后才落地。
    """
    dst_p = Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst_p.with_name(dst_p.name + ".rtools.tmp")

    text = "".join(sorted(chars)) or " "
    expected = _expected_keep(font, chars)
    keep_before = len(font.getBestCmap() or {})
    try:
        subsetter = subset.Subsetter(options=_subset_options())
        subsetter.populate(text=text)
        subsetter.subset(font)
        keep_after = len(font.getBestCmap() or {})
        _sanity_check(expected, keep_after)
        font.save(tmp)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    new_size = tmp.stat().st_size
    tmp.replace(dst_p)
    return {"new_size": new_size,
            "glyphs_before": keep_before, "glyphs_after": keep_after}


def subset_font(src: str, dst: str, chars: set[str]) -> dict:
    """把字体中未使用的字形剔除，输出到 dst（dst 可与 src 相同，原地）。

    返回 {原体积, 新体积, 保留字形数}。失败抛异常，由调用方记录。

    安全策略：永远先写临时文件，确认变小后才替换目标。
    旧版本直接写 dst，原地模式下若结果没变小会误删原字体，已修复。
    """
    src_p, dst_p = Path(src), Path(dst)
    old_size = src_p.stat().st_size
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst_p.with_name(dst_p.name + ".rtools.tmp")

    # fontTools 的 text 参数直接吃字符串；空字符集会导致字体失效，保底兜住
    text = "".join(sorted(chars)) or " "

    font = TTFont(src, fontNumber=0, lazy=True)
    expected = _expected_keep(font, chars)
    keep_before = len(font.getBestCmap() or {})
    try:
        subsetter = subset.Subsetter(options=_subset_options())
        subsetter.populate(text=text)
        subsetter.subset(font)
        keep_after = len(font.getBestCmap() or {})
        _sanity_check(expected, keep_after)
        font.save(tmp)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        font.close()

    new_size = tmp.stat().st_size
    # 防御：瘦身结果反而变大（极小字体可能发生），原文件不动
    if new_size >= old_size:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"瘦身结果未变小（{old_size} -> {new_size}），已跳过")

    tmp.replace(dst_p)
    return {"old_size": old_size, "new_size": new_size,
            "glyphs_before": keep_before, "glyphs_after": keep_after}
