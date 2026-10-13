"""依赖清单与冻结清单（lockfile）自洽性守卫。

背景（2026-10-13 v0.16.2）：requirements 用的是 `>=` 下限，仓库过去没有
lockfile，于是出现两个只有跑起来才会发现的洞——
1. 下限写得太旧：pillow>=10.0 / py7zr>=0.21 在旧 Python 上会解析到带 CVE 的
   版本，而 Dependabot 只看"实际解析出的版本"，这类洞它永远不报；
2. requirements-lock.txt 是手写的，写错、漏包、和清单脱节都没有护栏。

本文件把这两件事变成机械核对：lock 必须是精确 pin、必须覆盖两份清单的每个
直接依赖且不低于其下限、安全底线版本不许回落。顺序不影响判定（排序只是美观）。

版本比较一律用 packaging.version.Version，不用自写的元组切分：后者会把
预发布/dev/post 标记归零（12.3.0rc1 被当成等于 12.3.0），而 CVE 修复版
往往先以 rc 形式流出——那正是守卫唯一该拦住的情形。packaging 是 pytest
的依赖，已显式写进 requirements-dev.txt，不靠传递依赖偷用。
"""
from __future__ import annotations

import re
from pathlib import Path

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parent.parent

MANIFESTS = ("requirements.txt", "requirements-dev.txt")

# 安全底线：锁到"首个修复该包已知高危 CVE 的版本"，低于它就等于放行有洞的包。
# 来源：GHSA/CVE —— pillow CVE-2026-59205/59204/59200/59199（high，12.3.0 修）、
# py7zr CVE-2026-23879（high，1.1.3 修；0.20.0 及以下另有 CVE-2022-44900 critical）。
# 上游若再出修复版，抬下限的同时记得改这里。
SECURITY_BASELINE = {
    "pillow": "12.3.0",
    "py7zr": "1.1.3",
}

# exe 构建工具必须被冻结：否则发版机每次装的 PyInstaller 都不一样，
# 而内联 `pip install pyinstaller` 不在任何清单里、Dependabot 看不见它。
BUILD_TOOLS = ("pyinstaller",)

# 清单行只接受“name>=X.Y.Z”与“name==X.Y.Z”两种写法：extras（name[extra]）、
# 环境标记（; python_version>=...）、裸包名、<= / != / 区间限定一律当场拒收，
# 不静默当成“没有下限”放过（那会让守卫失去牙）。
_REQ_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)\s*(?P<op>>=|==)\s*(?P<ver>\d[^\s;#\[]*)$"
)


def _canon(name: str) -> str:
    """PEP 503 归一化包名（大小写与 -_. 差异不作区别）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _ver(text: str) -> Version:
    """PEP 440 版本比较入口：非标准版本串直接报错，不当 0 处理。"""
    try:
        return Version(text.strip())
    except InvalidVersion as exc:
        raise AssertionError(f"无法解析的版本号：{text!r}") from exc


def _read_lines(filename: str) -> list:
    """读清单有效行。只剔行首或空白前的 # 注释（pip 规则），
    因此本守卫假定清单不用 URL/片段依赖（name @ https://...#egg=...）。"""
    path = ROOT / filename
    assert path.is_file(), f"缺少 {filename}"
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if line:
            lines.append(line)
    return lines


def _lock_map() -> dict:
    """lock 的 name->version 表；同名重复直接报错（静默后写覆盖前写会藏住洞）。"""
    out: dict = {}
    for line in _read_lines("requirements-lock.txt"):
        if "==" not in line:
            continue
        name, _, ver = line.partition("==")
        key = _canon(name.strip())   # 去空格：防 `pkg ==1.0` 与 `pkg==1.0` 归一化后并存
        assert key not in out, (
            f"requirements-lock.txt 里 {name.strip()} 出现多次"
            f"（{out.get(key)} 与 {ver.strip()}），请重生成而不是手改"
        )
        out[key] = ver.strip()
    return out


def test_lock_entries_are_exact_pins():
    """lock 的每一行都必须是 name==version：不接受 >=，也不接受 -e / URL 依赖。"""
    for line in _read_lines("requirements-lock.txt"):
        assert "==" in line, f"lock 中出现非精确 pin 的行：{line!r}"
        name, _, ver = line.partition("==")
        assert name.strip() and ver.strip(), f"lock 行残缺：{line!r}"
        assert not line.startswith(("-", "http://", "https://", "file:")), (
            f"lock 混入非常规条目：{line!r}"
        )
        assert "==" not in ver, f"lock 行重复拆分：{line!r}"
        _ver(ver)  # 版本号本身必须合 PEP 440


def test_manifest_direct_deps_are_pinned_not_below_floor():
    """两份清单的每个直接依赖都要在 lock 里出现，且冻结版本不低于清单下限。"""
    locked = _lock_map()
    for filename in MANIFESTS:
        for line in _read_lines(filename):
            m = _REQ_RE.match(line)
            assert m, (
                f"{filename} 的依赖行必须写成 name>=X.Y.Z 或 name==X.Y.Z；"
                f"extras / 环境标记 / 裸包名 / <= 本守卫不支持：{line!r}"
            )
            name, op, floor = m.group("name"), m.group("op"), m.group("ver")
            assert op == ">=", (
                f"{filename} 里的 {name} 用了 {op}；两份清单只写 >= 下限，"
                "精确 pin 一律交给 requirements-lock.txt"
            )
            name = _canon(name)
            assert name in locked, (
                f"{name} 在 {filename} 里有下限但 lock 里没有对应 pin，"
                "说明 lock 过期，请按 requirements-lock.txt 头部口径重生成"
            )
            assert _ver(locked[name]) >= _ver(floor), (
                f"lock 中 {name}=={locked[name]} 低于 {filename} 的下限 >={floor}"
            )


def test_security_baseline_not_regressed():
    """清单下限与 lock 都不得跌回已知有洞的版本（防手抖改小、防 lock 用旧环境重生成）。"""
    locked = _lock_map()
    # 同一包可能在两份清单里各写一个下限：逐条参与对账（后写不得掩盖前写），
    # 取到的是“哪份清单写了多低”的全量清单，而不是 last-wins。
    floors: dict = {}
    for filename in MANIFESTS:
        for line in _read_lines(filename):
            m = _REQ_RE.match(line)
            if m:
                floors.setdefault(_canon(m.group("name")), []).append(m.group("ver"))
    for name, baseline in SECURITY_BASELINE.items():
        assert name in floors, f"{name} 已从清单里消失，安全底线无人看守"
        for got in floors[name]:
            assert _ver(got) >= _ver(baseline), (
                f"{name} 清单下限 {got} 低于安全底线 {baseline}"
            )
        assert name in locked, f"{name} 不在 lock 里，构建可能装到未审计版本"
        assert _ver(locked[name]) >= _ver(baseline), (
            f"{name} 冻结版本 {locked[name]} 低于安全底线 {baseline}"
        )


def test_build_tool_is_frozen():
    """PyInstaller 走内联安装，只有 lock 冻结它才可复现、可被 Dependabot 盯上。"""
    locked = _lock_map()
    for tool in BUILD_TOOLS:
        assert _canon(tool) in locked, f"{tool} 未被 lock 冻结，exe 构建不可复现"


def test_lock_body_is_sorted():
    """body 必须按整行 casefold 升序（与 requirements-lock.txt 头部重生成口径逐字一致）。

    固定顺序不是为了好看：乱序的 lock 会让下次 `pip freeze` 重生成产生整片 diff，
    把“到底哪个包变了”埋进噪声里，而依赖审查只需要看那一两行。
    """
    body = _read_lines("requirements-lock.txt")
    assert body == sorted(body, key=str.casefold), (
        "requirements-lock.txt 的 pin 行乱序，请按该文件头部口径重生成（而不是手改）"
    )
