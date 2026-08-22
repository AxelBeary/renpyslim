"""图片优化：PNG/JPG/WebP 压缩与格式转换（Pillow）。"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from PIL import Image


class OptimizeResult(dict):
    """优化器三态返回值（第二波修复，三个优化器共用）。

    契约（记账以 status 字段为准，不依赖真值判断）：
    - status="ok"：优化成功并已落盘。真值；含 path/old_size/new_size，
      兼容旧 dict 访问（res["old_size"] / res["new_size"] / res["converted"]）；
    - status="skipped"：未换格式且未动目标文件（压完没变小/已是最优/
      格式不支持/动图等）。假值；可含 reason 说明原因；
    - status="failed"：真错误，目标文件未动。假值；可含 reason。

    真值行为 = (status == "ok")：旧调用方（如 apk.py）的 `if res:`
    语义与旧版 Optional[dict] 完全一致（None → 假，成功字典 → 真），
    不会把 skipped/failed 误判为成功；但流水线记账必须显式读
    status，因为假值里也要区分 skipped（不计失败）与 failed。
    """

    def __bool__(self) -> bool:
        return self.get("status") == "ok"


def optimize_image(src: str, dst: str, quality: int,
                   convert_webp: bool = False) -> OptimizeResult:
    """优化单张图片。dst 可与 src 相同（原地）。

    convert_webp=True 时把 PNG/JPG 转成 WebP（dst 扩展名须为 .webp）。
    三态返回（见 OptimizeResult）：成功 ok（含 old_size/new_size/converted）；
    没变小/动图/格式不支持归 skipped；真错误归 failed。

    安全策略：永远先写到临时文件，确认体积确实变小后才替换目标，
    保证任何失败路径下原文件完好无损。
    """
    src_p, dst_p = Path(src), Path(dst)
    # 审核修复（高-3）：tmp 名带随机后缀——并行任务（如 a.wav 转换
    # 与 a.ogg 原地重编码）共用固定 tmp 名会互相踩踏
    tmp = dst_p.with_name(f"{dst_p.name}.rtools.{uuid.uuid4().hex[:8]}.tmp")

    try:
        old_size = src_p.stat().st_size
        ext = src_p.suffix.lower()
        with Image.open(src) as im:
            im.load()
            # 动图不处理：保存只会留下第一帧，会破坏游戏资源
            if getattr(im, "is_animated", False):
                return OptimizeResult(status="skipped",
                                      reason="动图不处理（只保存第一帧会弄坏资源）")

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
                return OptimizeResult(status="skipped",
                                      reason=f"不支持的图片格式 {ext}")

        new_size = tmp.stat().st_size
        if new_size >= old_size:
            tmp.unlink(missing_ok=True)
            return OptimizeResult(status="skipped", reason="已是最优，压不出更小")

        # 审核补修（低）：与 quantize_png 对齐，目标父目录不存在时先建
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(dst_p)
        return OptimizeResult(status="ok", path=str(dst_p),
                              old_size=old_size, new_size=new_size,
                              converted=dst_p.suffix.lower() != ext)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return OptimizeResult(status="failed", reason=str(e))


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
    # 审核修复（高-3）：同 optimize_image，tmp 名带随机后缀防并行踩踏
    tmp = dst_p.with_name(f"{dst_p.name}.rtools.{uuid.uuid4().hex[:8]}.tmp.png")
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
