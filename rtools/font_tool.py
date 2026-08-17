"""独立字体瘦身：不依赖游戏工程，选字体 + 选文本来源即可瘦身。

- TTF/OTF：输出 <原名>-slim.<ext>，永不覆盖原件
- TTC/OTC 集合：拆开后逐个字重瘦身，输出 <原名>-1-slim.<ext> 等
  （瘦身库无法写回集合格式，拆分输出是行业标准做法）
- 同步导出"使用字符清单.txt"，方便核对或喂给其他工具
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from . import charset
from .config import CharsetOptions
from .font_optimizer import subset_font, subset_font_object
from .models import Progress


class FontSlimError(Exception):
    pass


SINGLE_EXTS = {".ttf", ".otf"}
COLLECTION_EXTS = {".ttc", ".otc"}


def _unique_path(candidate: Path) -> Path:
    """目标已存在就加序号，绝不覆盖任何现有文件。"""
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(2, 100):
        alt = candidate.with_name(f"{stem}-{i}{suffix}")
        if not alt.exists():
            return alt
    raise FontSlimError("同名输出文件太多，请手动清理后重试")


def _face_ext(font) -> str:
    """按字体的实际类型决定输出扩展名。"""
    return ".otf" if getattr(font, "sfntVersion", "") == "OTTO" else ".ttf"


def write_charlist(chars: set[str], dest: str) -> str:
    """导出字符清单：排序后每行 40 个字符，便于阅读。"""
    ordered = sorted(c for c in chars if c.isprintable())
    lines = ["".join(ordered[i:i + 40]) for i in range(0, len(ordered), 40)]
    dest_p = Path(dest)
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    dest_p.write_text("\n".join(lines) + f"\n\n共 {len(ordered)} 个字符\n",
                      encoding="utf-8")
    return str(dest_p)


def run_font_slim(font_path: str, text_sources: list[str],
                  options: Optional[CharsetOptions] = None,
                  output_dir: Optional[str] = None,
                  progress: Progress | None = None) -> dict:
    """独立字体瘦身主流程。返回结果 dict（含每个输出文件的信息）。"""
    p = progress or Progress()
    opts = options or CharsetOptions()

    fp = Path(font_path)
    ext = fp.suffix.lower()
    if ext not in SINGLE_EXTS | COLLECTION_EXTS:
        raise FontSlimError(
            f"不支持的字体类型：{ext}。支持 ttf / otf / ttc / otc。")
    if not fp.exists():
        raise FontSlimError(f"字体文件不存在：{font_path}")
    if not text_sources:
        raise FontSlimError("请至少提供一个文本来源（文件或文件夹）。")

    out_dir = Path(output_dir) if output_dir else fp.parent

    # 第 1 步：提取字符集
    p.emit("charset", "正在从文本来源提取字符集……")
    chars, warnings = charset.extract_charset_sources(text_sources, opts)
    p.emit("charset", f"字符集就绪：{len(chars)} 个字符（含保底字符）")

    # 第 2 步：瘦身
    outputs = []
    if ext in SINGLE_EXTS:
        out = _unique_path(out_dir / f"{fp.stem}-slim{ext}")
        p.emit("slim", f"正在瘦身 {fp.name}……")
        try:
            res = subset_font(str(fp), str(out), chars)
            outputs.append({
                "src": fp.name, "out": str(out),
                "old_size": res["old_size"], "new_size": res["new_size"],
                "glyphs_before": res["glyphs_before"],
                "glyphs_after": res["glyphs_after"],
            })
        except ValueError as e:
            # 空壳防御拦截或已无瘦身空间：输出原样副本，把原因说清楚
            shutil.copyfile(fp, out)
            warnings.append(
                f"{fp.name}：本次未产出瘦身版本，输出为原样副本。原因：{e}")
            sz = fp.stat().st_size
            outputs.append({
                "src": fp.name, "out": str(out),
                "old_size": sz, "new_size": sz,
                "glyphs_before": None, "glyphs_after": None,
            })
    else:
        # TTC/OTC：拆开后逐个字重瘦身
        from fontTools.ttLib import TTCollection
        p.emit("slim", f"检测到字体集合 {fp.name}，正在拆分字重……")
        try:
            col = TTCollection(str(fp), lazy=True)
        except Exception as e:
            raise FontSlimError(f"字体集合打不开：{e}")
        # 审核修复（中-24）：TTCollection 句柄正常/异常路径都要关，
        # Windows 上被占句柄会让后续原地替换 PermissionError
        try:
            fonts = col.fonts
            for i, font in enumerate(fonts, start=1):
                face_ext = _face_ext(font)
                out = _unique_path(out_dir / f"{fp.stem}-{i}-slim{face_ext}")
                p.emit("slim", f"正在瘦身第 {i}/{len(fonts)} 个字重……")
                try:
                    res = subset_font_object(font, str(out), chars)
                    outputs.append({
                        "src": f"{fp.name}（字重 {i}）", "out": str(out),
                        "old_size": None, "new_size": res["new_size"],
                        "glyphs_before": res["glyphs_before"],
                        "glyphs_after": res["glyphs_after"],
                    })
                except Exception as e:
                    warnings.append(f"字重 {i} 瘦身失败，已跳过：{e}")
        finally:
            try:
                col.close()
            except Exception:
                pass

    if not outputs:
        raise FontSlimError("没有任何字重瘦身成功，请检查字体文件。")

    # 第 3 步：导出字符清单
    charlist = write_charlist(chars, str(out_dir / f"{fp.stem}-字符清单.txt"))
    p.emit("done", f"完成：生成 {len(outputs)} 个瘦身字体 + 字符清单")

    return {
        "font": fp.name,
        "charset_size": len(chars),
        "outputs": outputs,
        "charlist": charlist,
        "warnings": warnings,
    }
