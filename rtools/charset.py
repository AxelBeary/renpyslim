"""字符集提取：扫描项目文本文件，收集实际使用的字符。

四层防线中的第 1 层（全项目扫描）。保底字符集由 config.CharsetOptions
提供（第 2 层），手动追加由 extra_chars 提供（第 3 层）。

多语言感知（2026-08-25 补第一天起的缺口）：文本按翻译语言分桶，
字符集始终取全语言合集（用本工具就是发多语言包）；语言级的精确
交给“字体按实际服务的语言定向瘦身”，绝不做单语言发行过滤。
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import SKIP_DIRS, TEXT_EXTS
from .config import CharsetOptions

# 检测"玩家可输入任意文字"的写法，出现则字体瘦身有风险
DYNAMIC_INPUT_RE = re.compile(r"\brenpy\.input\s*\(|(?<![\w.])input\s*\(")

# Ren'Py 翻译目录约定：game/tl/<语言>/；tl/None/ 存默认语言的覆盖项，
# 属公共内容。
TL_DIR = "tl"
TL_BASE_LANG = "None"

# 行内换字体标签：{font=路径}…{/font}。标签内的字由该字体显示，
# 是"少用字体精确归属"的唯一可靠依据（样式引用无法静态精确归属）。
FONT_TAG_RE = re.compile(
    r"\{font\s*=\s*[\"']?([^{}\"']+?)[\"']?\s*\}(.*?)\{/font\}", re.DOTALL)
# 标签体内可能嵌套其他文本标签（{b}{size=…} 等），它们不提供字形，先剔掉
INNER_TAG_RE = re.compile(r"\{[^{}]*\}")

# 分桶键：主剧本 + tl/None + tl 外的内容（公共内容，永远计入）
BASE_BUCKET = "base"

# 成品根下的引擎目录：代码文本不是游戏内容，字符集扫描跳过；
# 引擎内置界面的用字另行定向收集（见 _engine_safety_chars）。
ENGINE_DIRS = ("renpy", "lib")


def detect_languages(root: str) -> list[str]:
    """列出游戏里的翻译语言（tl/ 下的目录名，排除 None）。

    root 可以是 game/ 目录，也可以是工程/成品根（自动再探一层 game/）。
    找不到翻译目录返回空列表。
    """
    root_p = Path(root)
    langs: set[str] = set()
    for tl in (root_p / TL_DIR, root_p / "game" / TL_DIR):
        if not tl.is_dir():
            continue
        for d in tl.iterdir():
            # 跳过嵌套的 tl 目录（个别工程里有 tl/tl/ 的异常结构，
            # "tl" 是翻译根目录保留名，不可能成为语言名）
            if d.is_dir() and d.name not in (TL_BASE_LANG, TL_DIR):
                langs.add(d.name)
    return sorted(langs)


def _tl_lang_of(rel_parts: tuple) -> str | None:
    """路径若属于某个语言的翻译目录，返回语言名；否则 None。

    兼容 game/tl/…（成品根）与 tl/…（game 根）两种相对路径。
    tl/None/ 与 tl 外的文件一律返回 None（视为公共内容，永不过滤）。
    """
    parts = rel_parts[1:] if rel_parts and rel_parts[0] == "game" else rel_parts
    # tl/None 与嵌套的 tl/tl/… 异常结构都视为公共内容，永不过滤
    if (len(parts) >= 2 and parts[0] == TL_DIR
            and parts[1] not in (TL_BASE_LANG, TL_DIR)):
        return parts[1]
    return None


def lang_of_script_rel(rel: str) -> str | None:
    """脚本文件的语言归属：tl/<语言>/… -> 语言名；否则 None（主剧本/公共）。"""
    return _tl_lang_of(Path(rel.replace("\\", "/")).parts)


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


def scan_language_buckets(root: str) -> tuple[dict, list]:
    """一遍扫描，把文本字符按语言归属分桶。

    返回 (buckets, dynamic_input_files)：buckets 键为语言名或 BASE_BUCKET
    （主剧本 / tl/None / tl 外）。rpyc 只收可打印字符，文本文件全收。
    分桶是“语言 × 字体字表”的地基。内部走统一的 scan_charset_tables，
    与标签归属/污染扫描共享同一遍扫描，不重复读盘解压。
    """
    buckets, _, _, dynamic_input_files = scan_charset_tables(root)
    return buckets, dynamic_input_files


def scan_charset_tables(root: str) -> tuple:
    """一遍扫描产出全部多语言字表：(语言分桶, 标签分桶, 插值污染集, 动态输入文件)。

    字符集合并、字体标签归属、精确瘦身污染判定共用本函数，
    大工程不再全树读三遍、rpyc 不再重复解压。
    """
    root_p = Path(root)
    buckets: dict[str, set[str]] = {}
    tag_buckets: dict[str, dict[str, set[str]]] = {}
    tainted: set[str] = set()
    dynamic_input_files: list[str] = []
    for p in _iter_text_files(root_p):
        lang = _tl_lang_of(p.relative_to(root_p).parts) or BASE_BUCKET
        suffix = p.suffix.lower()
        if suffix in (".rpyc", ".rpymc"):
            text = read_rpyc_text(p)
            # 编译产物含 pickle 操作码等不可打印字节，只收可打印字符，
            # 与成品模式口径保持一致，不给字体瘦身引入垃圾字形需求
            buckets.setdefault(lang, set()).update(
                c for c in text if c.isprintable())
        else:
            text = read_text_robust(p)
            if not text:
                continue
            buckets.setdefault(lang, set()).update(text)
            if suffix in (".rpy", ".rpym", ".py") and DYNAMIC_INPUT_RE.search(text):
                dynamic_input_files.append(p.relative_to(root_p).as_posix())
        # 行内 {font=…} 标签：归属分桶 + 插值污染标记同一遍完成；
        # 编译脚本里的对白同样含标签文本，一并计入。
        lang_map = tag_buckets.setdefault(lang, {})
        for m in FONT_TAG_RE.finditer(text):
            key = m.group(1).strip().replace("\\", "/")
            if "[" in m.group(2):
                tainted.add(key)
            got = {c for c in INNER_TAG_RE.sub("", m.group(2)) if c.isprintable()}
            if got:
                lang_map.setdefault(key, set()).update(got)
    return buckets, tag_buckets, tainted, dynamic_input_files


def merge_charset(buckets: dict, dynamic_input_files: list,
                  options: CharsetOptions) -> tuple[set[str], list[str]]:
    """把按语言分桶的字符合并为全语言字符集，产出警告。

    口径：字体瘦身永远按“多语言大包”服务，字符集取全部语言的并集；
    语言级的精确交给逐字体的语言定向瘦身，不在这里做单语言过滤。
    """
    warnings: list[str] = []
    chars: set[str] = set()
    for got in buckets.values():
        chars |= got

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


def extract_charset(root: str, options: CharsetOptions
                    ) -> tuple[set[str], list[str]]:
    """扫描 root 下所有文本文件，返回 (字符集合, 全局警告列表)。

    工程模式下 root 通常指向 game/ 目录（也兼容指向工程根）。
    内部走分桶扫描 + 全语言合并；分桶产物另供字体语言定向瘦身使用。
    """
    buckets, dynamic_input_files = scan_language_buckets(root)
    return merge_charset(buckets, dynamic_input_files, options)


def extract_charset_dist(root: str, options: CharsetOptions
                         ) -> tuple[set[str], list[str]]:
    """成品模式的字符集提取：脚本是编译后的 .rpyc，无法按文本解析。

    采用解压扫描法：.rpyc 是 zlib 压缩的 pickle，先解压再按
    utf-8 宽容解码收集可打印字符；pickle 操作码均为 ASCII，
    会被保底拉丁集覆盖，不会引入多余汉字。不反序列化 pickle，无安全风险。
    引擎目录（renpy/、lib/）的代码文本不计入游戏用字，但引擎内置界面
    （菜单/按键帮助等）的默认文案会真实上屏，单独定向收集后强制保留。
    """
    root_p = Path(root)
    chars: set[str] = set()

    for p in root_p.rglob("*"):
        if not p.is_file():
            continue
        parts = p.relative_to(root_p).parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        # 引擎目录不是游戏内容：收进来只会把字符集弄胖、拖慢扫描；
        # 内置界面用字由下方 _engine_safety_chars 定向兜住。
        if parts and parts[0] in ENGINE_DIRS:
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

    # 引擎内置界面用字强制保留（菜单/按键帮助等的默认文案未经翻译时
    # 直接上屏，丢了引擎目录扫描就得在这里兜住）
    chars.update(_engine_safety_chars(root_p))
    chars.update(options.base_text())
    chars.discard("\x00")
    warnings = [
        "成品模式下无法识别动态输入代码，字符集仅来自文件内容扫描，"
        "若游戏存在玩家打字输入，建议勾选更多保底字符集或手动追加常用字。"
    ]
    return chars, warnings


def _engine_safety_chars(root_p: Path) -> set[str]:
    """引擎内置界面（renpy/common）可能显示的字，强制保留。

    存档/读档/按键帮助等内置界面的默认文案未经翻译时直接上屏，
    跳过引擎目录扫描后若不补这一手，这些字会被字体瘦身剃掉出方框。
    按游戏自带引擎现扫现用（不同引擎版本内置文案有差异，不写死表）。
    无引擎目录（如安卓安装形态）返回空集。
    """
    common = root_p / "renpy" / "common"
    if not common.is_dir():
        return set()
    got: set[str] = set()
    for p in common.rglob("*"):
        if not p.is_file():
            continue
        suffix = p.suffix.lower()
        if suffix in (".rpyc", ".rpymc"):
            got.update(c for c in read_rpyc_text(p) if c.isprintable())
        elif suffix in TEXT_EXTS:
            got.update(read_text_robust(p))
    return got


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


def scan_font_tag_buckets(root: str) -> dict:
    """兼容门面：返回按语言分桶的 {font=…} 标签字符归属表。

    内部走统一的 scan_charset_tables，与字符集分桶共享同一遍扫描。
    """
    _, tag_buckets, _, _ = scan_charset_tables(root)
    return tag_buckets


def collect_font_tag_chars(root: str) -> dict[str, set[str]]:
    """兼容门面：把按语言分桶的标签字符合并为单张归属表（全语言并集）。"""
    merged: dict[str, set[str]] = {}
    for lang_map in scan_font_tag_buckets(root).values():
        for key, got in lang_map.items():
            merged.setdefault(key, set()).update(got)
    return merged


def tag_chars_for_font(rel: str, tag_map: dict[str, set[str]]) -> set[str]:
    """把各种引用写法归属到某字体资产的字符并集。

    匹配口径：完整相对路径 / 纯文件名 / 同基名（大小写不敏感）。
    同基名合并属于安全方向的放宽（多留字不会出方框）。
    """
    got: set[str] = set()
    for key, chars in tag_map.items():
        if font_keys_match(rel, {key}):
            got |= chars
    return got


def font_keys_match(rel: str, keys) -> bool:
    """rel 是否匹配 keys 集合里的任一引用写法（与归属同口径）。"""
    rel = rel.replace("\\", "/")
    bare = rel.rsplit("/", 1)[-1]
    for key in keys:
        if (key == rel or key == bare
                or key.rsplit("/", 1)[-1].lower() == bare.lower()):
            return True
    return False


def scan_tainted_font_keys(root: str) -> set[str]:
    """扫出标签体含 [插值] 的字体引用串集合（禁止进精确档的污染源）。

    插值处运行时显示什么无法静态预知，只留字面字符必出方框；
    因此只要某字体有任一带插值的标签，就不对它做精确归属。
    内部走统一的 scan_charset_tables，不重复扫描。
    """
    _, _, tainted, _ = scan_charset_tables(root)
    return tainted


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
