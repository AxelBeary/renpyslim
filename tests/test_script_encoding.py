"""仓库编码卫生：脚本文件的行尾与字符集（防"看起来跑了其实静默不执行"）。

为什么值得一组测试：.bat / .cmd 由 cmd.exe 按 **OEM 代码页**（zh-CN 上是
GBK/936）解析，不是 UTF-8。文件里只要有一个汉字，它的字节就可能吞掉后面的
换行，把下一行注释当成命令执行——症状是"双击没反应"或"跑了一半停住"，
而且完全没有任何报错。这类故障靠人眼评审发现不了，只能靠机械检查。

build_exe.bat 在 2026-11-28 之前就带着三行中文注释，属于实际存在的隐患
（见 docs/STATUS.md 的发版记录）。

配套的 .gitattributes 管住了行尾（*.bat text eol=crlf）；这里管住编码，
两者缺一不可。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# 由 cmd.exe 解析的脚本：必须纯 ASCII + CRLF，没有例外。
CMD_SCRIPTS = ("*.bat", "*.cmd")


def _cmd_script_files() -> list[Path]:
    found: list[Path] = []
    for pattern in CMD_SCRIPTS:
        for path in REPO.rglob(pattern):
            rel = path.relative_to(REPO).as_posix()
            if rel.startswith(".venv/") or rel.startswith("_tmp/"):
                continue
            found.append(path)
    return sorted(found)


@pytest.mark.parametrize("script", _cmd_script_files(),
                         ids=lambda p: p.relative_to(REPO).as_posix())
def test_cmd_script_is_pure_ascii(script: Path):
    """cmd.exe 只认 OEM 代码页：脚本里出现非 ASCII 字节就是隐患。"""
    data = script.read_bytes()
    offenders = [(i, b) for i, b in enumerate(data) if b > 0x7F]
    if offenders:
        offset, byte = offenders[0]
        line = data[:offset].count(b"\n") + 1
        pytest.fail(
            f"{script.relative_to(REPO).as_posix()} 第 {line} 行含非 ASCII 字节 "
            f"0x{byte:02X}（共 {len(offenders)} 个）。cmd.exe 按 GBK/936 解析 "
            f".bat，汉字字节会吞掉换行、把注释当命令执行。请改为纯 ASCII 注释。")


@pytest.mark.parametrize("script", _cmd_script_files(),
                         ids=lambda p: p.relative_to(REPO).as_posix())
def test_cmd_script_uses_crlf(script: Path):
    """cmd.exe 对孤立 LF 的容错并不一致，统一 CRLF 最省事。"""
    data = script.read_bytes()
    lone_lf = data.count(b"\n") - data.count(b"\r\n")
    lone_cr = data.count(b"\r") - data.count(b"\r\n")
    assert lone_lf == 0 and lone_cr == 0, (
        f"{script.relative_to(REPO).as_posix()} 行尾不统一："
        f"孤立 LF {lone_lf} 个、孤立 CR {lone_cr} 个，应全为 CRLF")


def test_gitattributes_keeps_bat_crlf():
    """属性表本身要被锁住，否则某次改动会把 .bat 的 eol 规则弄丢。"""
    attrs = REPO / ".gitattributes"
    assert attrs.is_file(), "缺少 .gitattributes（行尾统一依赖它）"
    text = attrs.read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in text
    assert "*.cmd text eol=crlf" in text


def test_no_python_source_committed_with_crlf():
    """入库的 Python 源码统一 LF：CRLF 会让 diff 全量重写、blame 失效。

    **查的是索引里的内容，不是磁盘上的检出副本。** 本仓库历史上有
    47 个 .py 的检出副本是 CRLF、而 blob 一直是 LF（检出侧的行尾
    转换留下的，没有 core.autocrlf 配置也会这样）。若对着磁盘断言，
    这组测试会在每台 Windows 机器上无意义地红，而真正该守的东西
    （提交进仓库的字节）反而没被看住。.gitattributes 的 eol=lf 负责
    让检出也收敛，这里只锁入库内容。
    """
    try:
        out = subprocess.run(["git", "ls-files", "*.py"],
                             cwd=REPO, capture_output=True, text=True,
                             timeout=60)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("POSIX 专属：依赖 git 可执行文件，当前环境不可用")
    if out.returncode != 0:
        pytest.skip("POSIX 专属：依赖 git 可执行文件，当前环境不可用")

    offenders = []
    for rel in out.stdout.splitlines():
        blob = subprocess.run(["git", "show", ":" + rel], cwd=REPO,
                              capture_output=True, timeout=60).stdout
        if b"\r\n" in blob:
            offenders.append(rel)
    assert not offenders, (
        "以下 Python 文件在仓库里含 CRLF 行尾，应统一为 LF："
        + ", ".join(offenders[:10]))
