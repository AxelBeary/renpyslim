"""默认配置：压缩档位、保底字符集、体积阈值。

所有默认值遵循需求基线：均衡档为开箱默认，默认安全可反悔。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------------------
# 压缩档位
# ---------------------------------------------------------------------------

@dataclass
class Preset:
    name: str
    label: str                 # 给人看的名称
    image_quality: int         # JPG/WebP 质量
    png_to_webp: bool          # 是否把 PNG 转成 WebP（仅工程模式，会改引用）
    audio_bitrate_k: int       # OGG 音频码率
    font_subset: bool          # 是否做字体瘦身
    min_size_kb: int           # 小于该体积的文件直接跳过（已降到极低，小文件也要榨）


PRESETS: dict[str, Preset] = {
    "conservative": Preset(
        name="conservative", label="保守档（画质优先）",
        # 用户拍板（2026-08-17）：默认首选画质优先——q95 接近视觉无损；
        # WebP q95 同样接近视觉无损但体积显著更小，故开启转换；
        # 体积门槛降到 1KB：几十 KB 的小图转 WebP 后只剩几 KB，
        # 数量又多，能榨的每一丝都榨干（多核放开后处理成本不再是理由）
        image_quality=95, png_to_webp=True, audio_bitrate_k=192,
        font_subset=True, min_size_kb=1,
    ),
    "balanced": Preset(
        name="balanced", label="均衡档",
        image_quality=85, png_to_webp=True, audio_bitrate_k=128,
        font_subset=True, min_size_kb=1,
    ),
    "aggressive": Preset(
        name="aggressive", label="激进档（体积优先）",
        image_quality=70, png_to_webp=True, audio_bitrate_k=96,
        font_subset=True, min_size_kb=1,
    ),
}
# 用户拍板（2026-08-17）：默认档位改为画质优先的保守档
DEFAULT_PRESET = "conservative"

# ---------------------------------------------------------------------------
# 体积阈值（分析报告用）
# ---------------------------------------------------------------------------

LARGE_IMAGE_KB = 1024          # 图片超过 1MB 标记为"偏大"
HUGE_IMAGE_KB = 4096           # 超过 4MB 标记为"巨大"
LARGE_AUDIO_KB = 2048          # 音频超过 2MB 提示关注
LARGE_FONT_KB = 4096           # 字体超过 4MB 提示瘦身收益大
LARGE_VIDEO_KB = 10240         # 视频超过 10MB 提示关注
HIGH_AUDIO_BITRATE = 192       # kbps 以上视为可压缩

# ---------------------------------------------------------------------------
# 保底字符集（字体瘦身的第二层防线）
# ---------------------------------------------------------------------------

# 基础拉丁 + 数字 + 常用 ASCII 标点（默认开启）
BASE_LATIN = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    " !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
)

# 常用中文标点（默认开启）
# 注意：弯引号必须用 \u 转义写——直接写进源码时编辑器/引号配对
# 容易把它截断成 ASCII 引号（曾因此把 “”‘’ 四个字符弄丢）。
BASE_CJK_PUNCT = (
    "，。！？、；："
    "\u201c\u201d\u2018\u2019"   # “”‘’
    "（）《》〈〉【】—…·～￥"
)

# 全角符号（默认关闭，可勾选）
FULLWIDTH_SYMBOLS = (
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    "０１２３４５６７８９"
    "　！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝￣"
)

# 日文假名（默认关闭，可勾选）
HIRAGANA = "".join(chr(c) for c in range(0x3041, 0x3097))
KATAKANA = "".join(chr(c) for c in range(0x30A1, 0x30F7)) + "ー・"


@dataclass
class CharsetOptions:
    """字体瘦身的保底字符集开关 + 手动追加。

    字符集永远取全语言合集（多语言大包口径）；语言级的精确由
    逐字体的语言定向瘦身实现，不提供单语言发行过滤。
    """
    base_latin: bool = True
    cjk_punct: bool = True
    fullwidth: bool = False
    kana: bool = False
    extra_chars: str = ""      # 用户手动追加的字符

    def base_text(self) -> str:
        parts = []
        if self.base_latin:
            parts.append(BASE_LATIN)
        if self.cjk_punct:
            parts.append(BASE_CJK_PUNCT)
        if self.fullwidth:
            parts.append(FULLWIDTH_SYMBOLS)
        if self.kana:
            parts.append(HIRAGANA + KATAKANA)
        if self.extra_chars:
            parts.append(self.extra_chars)
        return "".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 优化选项总开关（对应界面上的勾选项）
# ---------------------------------------------------------------------------

@dataclass
class OptimizeOptions:
    preset: str = DEFAULT_PRESET
    do_images: bool = True
    do_audio: bool = True
    do_fonts: bool = True
    do_videos: bool = False        # 视频默认关闭（二期功能，兼容性风险最高）
    convert_png_webp: bool = True  # 跟随档位；模式 B 下强制忽略
    charset: CharsetOptions = field(default_factory=CharsetOptions)
    delete_unreferenced: bool = False   # 模式 B：默认只标记不删除
    quarantine_unused: bool = False  # 工程模式：确认无引用的资源移入隔离区（默认只报告）
    png_quant: bool = False          # 实验性：PNG 有损量化深度压缩（BACKLOG B5）
    experimental_remap: bool = False  # 实验性：成品注入运行时重映射脚本（BACKLOG B9）
    experimental_av1: bool = False   # 实验性：视频用 AV1 编码（官方支持且更省，仅 8.0+ 引擎能放）
    experimental_decompile: bool = False  # 实验性：反编译 rpyc 解锁无源码成品的格式转换（unrpyc）
    use_cache: bool = True            # 增量缓存（BACKLOG B6）
    in_place: bool = False   # 直接修改原件（危险）：默认关，开启后流水线会强制先备份

    def preset_obj(self) -> Preset:
        return PRESETS.get(self.preset, PRESETS[DEFAULT_PRESET])

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def default_options() -> OptimizeOptions:
    return OptimizeOptions()
