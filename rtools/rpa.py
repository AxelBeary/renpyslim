"""RPA 封包读写：纯 Python 实现 Ren'Py 归档格式（RPA-2.0 / RPA-3.0）。

格式说明（逐行对照官方实现校准：写入器 launcher/game/archiver.rpy，
读取器 renpy/loader.py）：
- 头部在文件**开头**，一行 ASCII：
    RPA-3.0 <16位十六进制索引偏移> <8位十六进制混淆密钥>
    RPA-2.0 <16位十六进制索引偏移>
  官方写入器先写占位头部，写完后回到文件头覆写真实值。
- 索引偏移处开始是 zlib 压缩的 pickle 字典：
    { 文件名: [(偏移, 长度, 前缀字节)...] }
  RPA-3.0（官方现行写法）中索引里的偏移和长度都要与密钥异或还原；
  每个条目的前缀字段存前 25 字节（旧版混淆用，官方现行写空），
  文件体中对应位置的前缀是冗余副本，读取时跳过。
"""
from __future__ import annotations

import pickle
import zlib
from pathlib import Path
from typing import BinaryIO, Dict, List, Tuple

PREFIX_LEN = 25
OFFICIAL_KEY = 0x42424242   # 官方固定密钥（减少版本间差异）
HEADER_PLACEHOLDER = b"RPA-3.0 XXXXXXXXXXXXXXXX XXXXXXXX\n"

Entry = List[Tuple[int, int, bytes]]   # [(offset, length, prefix), ...]


class RpaError(Exception):
    pass


class _SafeUnpickler(pickle.Unpickler):
    """受限反序列化：RPA 索引只应包含基础数据类型。

    Ren'Py 封包格式规定索引是 pickle，无法换成 JSON；
    这里白名单限制只允许基础类型，防止恶意封包借 pickle 执行代码。
    """

    # 注意：pickle 协议 2 用 __builtin__ 而非 builtins，两个都要放行
    _SAFE_GLOBALS = {
        (mod, name)
        for mod in ("builtins", "__builtin__")
        for name in ("dict", "list", "tuple", "str", "int", "bytes", "bool", "float")
    }

    def find_class(self, module: str, name: str):
        if (module, name) in self._SAFE_GLOBALS:
            return super().find_class(module, name)
        # pickle 协议 2 用 _codecs.encode 构造 bytes，纯函数无副作用，放行
        if module == "_codecs" and name == "encode":
            import _codecs
            return _codecs.encode
        raise RpaError(f"封包索引含不安全类型：{module}.{name}")


def _loads_safe(raw: bytes):
    import io
    return _SafeUnpickler(io.BytesIO(raw)).load()


class RpaArchive:
    """只读方式打开一个 RPA 封包。"""

    def __init__(self, path: str):
        self.path = path
        self.version: str = ""
        self.key: int = 0
        self._entries: Dict[str, Entry] = {}
        self._f: BinaryIO = open(path, "rb")
        self._read_index()

    def _read_index(self) -> None:
        header = self._f.readline().strip().decode("ascii", "replace")
        parts = header.split(" ")
        if len(parts) >= 2 and parts[0] in ("RPA-2.0", "RPA-3.0"):
            self.version = parts[0]
            try:
                offset = int(parts[1], 16)
            except ValueError:
                raise RpaError(f"无法解析封包头部偏移：{self.path}")
            if self.version == "RPA-3.0":
                if len(parts) < 3:
                    raise RpaError(f"RPA-3.0 封包缺少密钥：{self.path}")
                # 兼容多密钥字段：官方引擎会把偏移后的所有十六进制字段
                # 全部异或成真实密钥（部分魔改工具会写多个字段）
                try:
                    key = 0
                    for fld in parts[2:]:
                        key ^= int(fld, 16)
                except ValueError:
                    raise RpaError(f"无法解析封包密钥：{self.path}")
                self.key = key
        else:
            self._f.close()
            raise RpaError(f"不是可识别的 RPA 封包：{self.path}")

        self._f.seek(offset)
        raw = self._f.read()
        try:
            index = _loads_safe(zlib.decompress(raw))
        except RpaError:
            raise
        except Exception as e:
            raise RpaError(f"封包索引损坏或版本不兼容：{self.path}（{e}）")
        if not isinstance(index, dict):
            raise RpaError(f"封包索引格式异常：{self.path}")

        # 归一化：条目可能是单个元组或元组列表；RPA-2.0 无前缀
        # RPA-3.0 有两代写法，用前缀字段自动区分：
        #   现行官方（8.x）：前缀为空，偏移、长度都异或
        #   旧版（7.x 及更早）：前缀存前 25 字节，只有偏移异或
        new_style = all(
            (seg[2] == b"" if len(seg) == 3 else True)
            for value in index.values()
            for seg in ([value] if isinstance(value, tuple) else value)
        )
        for name, value in index.items():
            if isinstance(value, tuple):
                value = [value]
            norm: Entry = []
            for seg in value:
                if len(seg) == 3:
                    off, length, prefix = seg
                    if not isinstance(prefix, bytes):
                        prefix = bytes(prefix)
                elif len(seg) == 2:
                    off, length = seg
                    prefix = b""
                else:
                    raise RpaError(f"封包条目格式异常：{name}")
                if self.version == "RPA-3.0":
                    off ^= self.key
                    if new_style:
                        length ^= self.key
                norm.append((off, length, prefix))
            self._entries[str(name)] = norm

    def names(self) -> List[str]:
        return sorted(self._entries.keys())

    def size(self, name: str) -> int:
        return sum(length for _, length, _ in self._entries[name])

    def read(self, name: str) -> bytes:
        """读取封包内一个文件的完整内容。"""
        if name not in self._entries:
            raise RpaError(f"封包内不存在文件：{name}")
        chunks = []
        for off, length, prefix in self._entries[name]:
            # 文件体中前 len(prefix) 字节是冗余副本，跳过
            self._f.seek(off + len(prefix))
            chunks.append(prefix + self._f.read(length - len(prefix)))
        return b"".join(chunks)

    def extract(self, name: str, dest: str) -> None:
        out = Path(dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self.read(name))

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass


class RpaWriter:
    """重建一个 RPA-3.0 封包，逐步骤对齐官方 archiver.rpy：
    开头写占位头部 -> 逐文件写入 -> 末尾写索引 -> 回头覆写真实头部。
    """

    def __init__(self, path: str, key: int | None = None):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.key = key if key is not None else OFFICIAL_KEY
        self._f = open(path, "wb")
        self._f.write(HEADER_PLACEHOLDER)
        self._entries: Dict[str, Entry] = {}

    def add(self, name: str, data: bytes) -> None:
        name = name.replace("\\", "/")
        # 官方在每个文件前写一段填充，这里保持一致，提高互操作性
        self._f.write(b"Made with Ren'Py.")
        offset = self._f.tell()
        self._f.write(data)
        # 官方现行格式：偏移、长度都异或，前缀为空
        self._entries[name] = [(offset ^ self.key, len(data) ^ self.key, b"")]

    def add_file(self, name: str, src: str) -> None:
        self.add(name, Path(src).read_bytes())

    def close(self) -> None:
        index_off = self._f.tell()
        self._f.write(zlib.compress(pickle.dumps(self._entries, 2), 3))
        self._f.seek(0)
        self._f.write(b"RPA-3.0 %016x %08x\n" % (index_off, self.key))
        self._f.close()

    def abort(self) -> None:
        """异常终止：只关句柄不写索引（半成品由调用方清理）。

        审核修复：以前异常路径上句柄不关，Windows 上临时封包
        被占住，后续 unlink 连环爆。
        """
        try:
            self._f.close()
        except Exception:
            pass


def rebuild_archive(src_rpa: str, dest_rpa: str,
                    replacements: Dict[str, str]) -> Tuple[int, int]:
    """复制重建一个封包：replacements 把 封包内路径 -> 优化后的本地文件路径。

    未替换的文件原样复制；重建时沿用源封包的密钥（兼容性最保守）。
    返回 (替换数, 总文件数)。
    """
    replaced = 0
    total = 0
    arc = RpaArchive(src_rpa)
    writer = RpaWriter(dest_rpa, key=arc.key if arc.version == "RPA-3.0" else 0)
    try:
        try:
            for name in arc.names():
                total += 1
                if name in replacements:
                    writer.add_file(name, replacements[name])
                    replaced += 1
                else:
                    writer.add(name, arc.read(name))
            writer.close()
        except Exception:
            writer.abort()
            raise
    finally:
        arc.close()
    return replaced, total
