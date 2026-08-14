"""图片优化：PNG/JPG/WebP 压缩与格式转换（Pillow）。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image


def optimize_image(src: str, dst: str, quality: int,
                   convert_webp: bool = False) -> Optional[dict]:
    """优化单张图片。dst 可与 src 相同（原地）。

    convert_webp=True 时把 PNG/JPG 转成 WebP（dst 扩展名须为 .webp）。
    返回 {old_size, new_size, converted}；结果没变小则什么都不动，返回 None。

    安全策略：永远先写到临时文件，确认体积确实变小后才替换目标，
    保证任何失败路径下原文件完好无损。
    """
    src_p, dst_p = Path(src), Path(dst)
    old_size = src_p.stat().st_size
    ext = src_p.suffix.lower()
    tmp = dst_p.with_name(dst_p.name + ".rtools.tmp")

    try:
        with Image.open(src) as im:
            im.load()
            # 动图不处理：保存只会留下第一帧，会破坏游戏资源
            if getattr(im, "is_animated", False):
                return None

            if convert_webp and ext in (".png", ".jpg", ".jpeg"):
                out = im.convert("RGBA") if ext == ".png" else im.convert("RGB")
                out.save(tmp, "WEBP", quality=quality, method=6)
            elif ext == ".png":
                # PNG 只做无损优化（量化有丢色风险，默认不做）
                im.save(tmp, "PNG", optimize=True)
            elif ext in (".jpg", ".jpeg"):
                out = im.convert("RGB") if im.mode not in ("RGB", "L") else im
                out.save(tmp, "JPEG", quality=quality, optimize=True,
                         progressive=True)
            elif ext == ".webp":
                im.save(tmp, "WEBP", quality=quality, method=6)
            else:
                return None
    except Exception:
        tmp.unlink(missing_ok=True)
        return None

    new_size = tmp.stat().st_size
    if new_size >= old_size:
        tmp.unlink(missing_ok=True)
        return None

    tmp.replace(dst_p)
    return {"old_size": old_size, "new_size": new_size,
            "converted": dst_p.suffix.lower() != ext}


def quantize_png(src: str, dst: str, max_colors: int = 256) -> Optional[dict]:
    """PNG 深度压缩（BACKLOG B5，实验性）：有损调色板量化。

    本地实现 TinyPNG 同类效果：真彩 PNG 精简到 ≤256 色，
    CG/立绘类大图通常再省 60%~80%。同名同格式，两种模式都安全。
    没变小或不适量化时返回 None，绝不动目标文件。
    """
    src_p, dst_p = Path(src), Path(dst)
    if src_p.suffix.lower() != ".png":
        return None
    old_size = src_p.stat().st_size
    tmp = dst_p.with_name(dst_p.name + ".rtools.tmp.png")
    try:
        with Image.open(src) as im:
            im.load()
            if getattr(im, "is_animated", False):
                return None
            if im.mode == "RGBA":
                # FASTOCTREE 支持带透明通道；LIBIMAGEQUANT 若可用则更优
                try:
                    q = im.quantize(colors=max_colors,
                                    method=Image.Quantize.LIBIMAGEQUANT)
                except Exception:
                    q = im.quantize(colors=max_colors,
                                    method=Image.Quantize.FASTOCTREE)
            elif im.mode == "P":
                # 本来就是调色板图，量化收益极小，不折腾
                return None
            else:
                q = im.convert("RGB").quantize(colors=max_colors)
            q.save(tmp, "PNG", optimize=True)
    except Exception:
        tmp.unlink(missing_ok=True)
        return None

    new_size = tmp.stat().st_size
    if new_size >= old_size:
        tmp.unlink(missing_ok=True)
        return None

    dst_p.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(dst_p)
    return {"old_size": old_size, "new_size": new_size, "converted": False}
