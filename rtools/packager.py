"""官方 SDK 打包调度：所有打包都委托给 Ren'Py 官方程序，绝不自制。

已核实的官方命令（Ren'Py 8.x，见 SDK launcher/game/distribute.rpy 与 android.rpy）：
- PC/Mac 发布包：  renpy.exe|renpy.sh launcher distribute <工程路径> [--package 名] [--destination 目录]
- Android APK：   renpy.exe|renpy.sh launcher android_build <工程路径> [--destination 目录]
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from . import archives as archives_mod
from . import sdk as sdk_mod
from .procutil import run_quiet

# 归档后缀识别走 archives.archive_suffix：.tar.bz2 这类双后缀必须按最长匹配，
# Path.suffix 只会给出 .bz2。打包产物清单按同一口径判定，
# 避免“产物明明生成了却不计数”（v0.18.0 / issue #26）。
CONFIG_DIR = Path.home() / ".renpytools"
CONFIG_FILE = CONFIG_DIR / "config.json"

# 界面上的平台选项 -> 官方 distribute 的 --package 名称
PLATFORM_PACKAGES = {
    "pc": "pc",        # Windows + Linux 合包
    "win": "win",
    "linux": "linux",
    "mac": "mac",
}

# 全部合法打包平台（GUI 与 CLI 共用同一口径）。android 走另一条官方
# 命令（android_build），所以不在 PLATFORM_PACKAGES 里，但同样是合法平台。
KNOWN_PLATFORMS = tuple(PLATFORM_PACKAGES) + ("android",)


def platform_error(platforms: list[str]) -> Optional[str]:
    """校验打包平台列表：合法返回 None，否则返回给人看的提示文案。

    Web 层直接返回这段文案即可，无需借异常传递消息（避免把异常信息
    带进 HTTP 响应）。
    """
    if not isinstance(platforms, (list, tuple)) or not platforms:
        return "至少选择一个打包平台，不能全不选。"
    unknown = [str(item) for item in platforms
               if str(item).strip() not in KNOWN_PLATFORMS]
    if unknown:
        return (f"未知的打包平台：{'、'.join(unknown)}；"
                f"可选：{', '.join(KNOWN_PLATFORMS)}")
    return None


def validate_platforms(platforms: list[str]) -> list[str]:
    """校验打包平台列表：空列表或未知平台直接报错，绝不静默过滤。

    CLI 与 Web 单一口径——凡是调用 package_project 的入口都先过这里，
    避免“选了平台却没打出来”的静默失败。返回去掉首尾空白的平台名。
    """
    error = platform_error(platforms)
    if error:
        raise ValueError(error)
    return [str(item).strip() for item in platforms]

# A 模式"打包时把资源封进 RPA"：写入一份构建配置，剩下的交给官方 SDK，
# 不自制封包逻辑（官方 launcher/game/archiver.rpy 同款写法）
ARCHIVE_CONFIG_NAME = "rtools_archive.rpy"
# 收口修复：模板标记——区分"本工具注入的文件"与"用户自己的同名文件"，
# 覆盖前先备份用户文件，清理时只删本工具注入的那份。
ARCHIVE_TEMPLATE_MARKER = "# 由 Ren'Py 工具箱自动生成"
USER_BACKUP_SUFFIX = ".user.bak"
_ARCHIVED_PATTERNS = (
    "**.png", "**.jpg", "**.jpeg", "**.webp", "**.bmp",
    "**.ogg", "**.mp3", "**.opus", "**.wav", "**.flac",
    "**.mp4", "**.webm", "**.ogv",
    "**.ttf", "**.otf",
)


def inject_archive_config(project_dir: str) -> tuple[str, bool]:
    """在工程里写入官方归档配置，打包时资源会自动封入 main.rpa。

    只封资源类文件；脚本、配置一律留在包外，不影响任何运行逻辑。
    重复注入本工具自己的文件时直接覆写，避免叠加。
    收口修复：若同名文件已存在且不含本工具模板标记（用户自己的文件），
    覆写前先备份为 *.user.bak，绝不无声覆盖用户内容。
    返回 (配置路径, 本次是否为用户文件创建了备份)。
    """
    cfg_path = Path(project_dir) / "game" / ARCHIVE_CONFIG_NAME
    backed_up = False
    if cfg_path.exists():
        try:
            existing = cfg_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            existing = ""
        if ARCHIVE_TEMPLATE_MARKER not in existing:
            bak_path = cfg_path.with_name(cfg_path.name + USER_BACKUP_SUFFIX)
            shutil.copy2(cfg_path, bak_path)
            backed_up = True
    lines = [f"{ARCHIVE_TEMPLATE_MARKER}：打包时把资源封入 main.rpa",
             "init python:",
             '    build.archive("main", "all")']
    for pat in _ARCHIVED_PATTERNS:
        lines.append(f'    build.classify("game/{pat}", "main")')
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(cfg_path), backed_up


def remove_archive_config(project_dir: str) -> None:
    """删除打包前自动注入的归档配置，恢复用户工程原状（审核修复）。

    该文件只为本次打包服务，留在工程里会污染用户的版本库；
    删除失败不抛异常，清理不应阻断打包结果的返回。
    收口修复：只删含本工具模板标记的文件（即本工具注入的那份）；
    用户自己的同名文件及其 .user.bak 备份一律保留。
    """
    try:
        cfg_path = Path(project_dir) / "game" / ARCHIVE_CONFIG_NAME
        if not cfg_path.exists():
            return
        text = cfg_path.read_text(encoding="utf-8", errors="ignore")
        if ARCHIVE_TEMPLATE_MARKER in text:
            cfg_path.unlink()
            # 用户原本就有同名文件：注入前备份成 .user.bak，这里必须
            # 放回原位，否则用户的活跃配置文件被无声删除。
            bak_path = cfg_path.with_name(cfg_path.name + USER_BACKUP_SUFFIX)
            if bak_path.exists():
                os.replace(bak_path, cfg_path)
    except OSError:
        pass


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                           encoding="utf-8")


def find_sdk(explicit: Optional[str] = None) -> Optional[str]:
    """定位 Ren'Py SDK：用户指定 > 配置文件 > 常见安装位置。"""
    if explicit and sdk_mod.is_sdk_dir(explicit):
        _remember_sdk(explicit)
        return explicit

    cfg = load_config()
    remembered = cfg.get("sdk_path")
    if remembered and sdk_mod.is_sdk_dir(remembered):
        return remembered

    # 常见位置按平台给：Windows 的老猜测列表在 POSIX 上毫无意义，
    # 反过来 Linux 的 ~/renpy 之类也不该塞进 Windows 的搜索顺序里。
    if os.name == "nt":
        guesses = [
            Path.home() / "renpy",
            Path("C:/renpy"), Path("D:/renpy"), Path("E:/renpy"),
            Path.home() / "Desktop" / "renpy",
        ]
    else:
        guesses = [
            Path.home() / "renpy",
            Path.home() / "renpy-sdk",
            Path.home() / "Desktop" / "renpy",
            Path.home() / "Applications" / "renpy",
            Path("/opt/renpy"),
            Path("/usr/local/renpy"),
        ]
    for g in guesses:
        if sdk_mod.is_sdk_dir(str(g)):
            _remember_sdk(str(g))
            return str(g)
    return None


def _remember_sdk(path: str) -> None:
    cfg = load_config()
    cfg["sdk_path"] = path
    save_config(cfg)


def sdk_version(sdk: str) -> str:
    """SDK 引擎版本字符串（形如 "Ren'Py 8.3.4.2201"）；拿不到返回"未知版本"。

    走 procutil.run_quiet：AV1 门控会在常规优化流程里调用本函数（issue #42），
    图形界面下裸 subprocess 会闪黑框（安全红线 9：外部调用一律无窗口）。
    """
    launcher = sdk_mod.find_launcher(sdk)
    if not launcher:
        return "未知版本"
    try:
        out = run_quiet([launcher, "--version"],
                        capture_output=True, timeout=60)
        return out.stdout.decode("utf-8", "replace").strip().splitlines()[0]
    except Exception:
        return "未知版本"


_VERSION_NUM_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_sdk_version(text: str) -> Optional[tuple[int, int, int]]:
    """从官方启动器 --version 的输出里解析引擎版本号；解析不出来返回 None。

    实际输出形如 "Ren'Py 8.3.4.2201"（末尾四段是构建号），也可能只给
    "Ren'Py 8.3" 这种两段式；这里只取前三段数字，缺的位补 0 再比较，
    避免 (8, 3) < (8, 0, 0) 这类元组长度陷阱。
    """
    if not text or text == "未知版本":
        return None
    match = _VERSION_NUM_RE.search(text)
    if not match:
        return None
    parts = [int(g) if g else 0 for g in match.groups()]
    return tuple((parts + [0, 0, 0])[:3])


def _av1_engine_version(game_dir,
                        sdk: Optional[str] = None
                        ) -> tuple[Optional[tuple[int, int, int]],
                                   Optional[str]]:
    """取判定 AV1 要用的目标引擎版本（av1_decision 的内部步骤，勿单独调）。

    取值顺序（fail-closed）：
    1. game/script_version.txt——成品里由 Ren'Py 生成，最权威；
    2. 没有该文件时用本机 SDK 版本兜底：工程模式多半还没生成版本文件，
       实际打包用的就是本机 SDK，所以 SDK 版本即目标引擎版本；
    3. 两者都拿不到 → 返回 (None, None)，调用方必须按"不支持"处理。

    返回 (版本三元组, 来源说明)；来源是给人看的短句，用于 warning 文案。
    版本文件存在但解析失败时**不**回退到 SDK：那份文件说的就是引擎版本，
    解析不出来只能当作"拿不到"，再拿本机 SDK 猜等于掩盖问题。
    """
    sv = Path(game_dir) / "script_version.txt"
    if sv.is_file():
        try:
            raw = sv.read_text(encoding="utf-8", errors="ignore").strip()
            version = tuple(int(x) for x in ast.literal_eval(raw))
            return version, "script_version.txt"
        except Exception:
            return None, None
    # sdk 是调用方显式指定的 SDK（如 CLI 的 --sdk）；不传就按常规位置探测。
    # 工程模式在打包前就判定 AV1，此时显式 SDK 还没被 find_sdk 记住，
    # 必须由调用方直接传进来，否则首跑会误判成"拿不到 SDK"。
    sdk_dir = find_sdk(sdk)
    if not sdk_dir:
        return None, None
    version = parse_sdk_version(sdk_version(sdk_dir))
    if version is None:
        return None, None
    return version, "本机 SDK"


# AV1 视频只有 Ren'Py 8.0+ 的引擎能播放（官方支持清单见 video_optimizer 头注释）
AV1_MIN_ENGINE_VERSION = (8, 0, 0)

# "为什么停用 AV1" 的中文提示：工程模式与成品模式共用同一句话，
# 结尾必须说清怎么解除停用（issue #42 的口径）。
_MIN_VERSION_TEXT = ".".join(str(x) for x in AV1_MIN_ENGINE_VERSION[:2])
_MINS_TUPLE_TEXT = "(" + ", ".join(str(x) for x in AV1_MIN_ENGINE_VERSION) + ")"
AV1_DISABLED_WARNING = (
    "AV1 视频编码已跳过：无法确认目标引擎版本"
    f"（AV1 要求 Ren'Py {_MIN_VERSION_TEXT} 及以上），"
    "为免老引擎播放失败，本次按普通编码处理。"
    "解除办法：在 game/script_version.txt 里写入目标引擎版本"
    f"（如 (8, 3, 4)，只要不低于 {_MINS_TUPLE_TEXT}），"
    f"或在设置里指定 {_MIN_VERSION_TEXT} 及以上的 SDK 目录"
    "（full / package 也可以加 --sdk）。")


def av1_decision(options, game_dir,
                 sdk: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """AV1 的最终生效值 + 判定依据：工程/成品两条线唯一的门控入口。

    返回 (是否生效, 版本来源)。来源是 "script_version.txt" / "本机 SDK"，
    用于把"按什么判定的"写进提示文案；未生效时来源为 None。

    口径（fail-closed，issue #42）：
    - 用户没开 experimental_av1 → 不生效，且不去起 `renpy --version` 子进程；
    - 有 game/script_version.txt → 按它判定（解析不出来就当作拿不到，
      绝不拿本机 SDK 去猜）；
    - 没有该文件 → 用本机 SDK 版本兜底；
    - 都拿不到 → 不生效，调用方按普通编码处理并给出 AV1_DISABLED_WARNING。

    run_project 与 run_dist 都只调这一个函数，各自不再写一份 if。
    """
    if not getattr(options, "experimental_av1", False):
        return False, None
    version, source = _av1_engine_version(game_dir, sdk)
    if version is None:
        return False, None
    return tuple(version) >= AV1_MIN_ENGINE_VERSION, source


def check_environment(sdk: Optional[str] = None) -> dict:
    """环境体检：SDK / FFmpeg / Java（安卓需要）。"""
    sdk_path = find_sdk(sdk)
    result = {
        "sdk_path": sdk_path,
        "sdk_version": sdk_version(sdk_path) if sdk_path else None,
        "ffmpeg": shutil.which("ffmpeg"),
        "java": shutil.which("java"),
        "rapt": bool(sdk_path and (Path(sdk_path) / "rapt").exists()),
    }
    result["android_ready"] = bool(result["java"] and result["rapt"])
    return result


def _clean_build_log(text: str, keep: int = 40) -> str:
    """过滤打包日志里的刷屏进度行，只留有信息量的行。"""
    lines = text.replace("\r", "\n").split("\n")
    useful = [ln.strip() for ln in lines if ln.strip()
              and not ln.strip().startswith("Writing the android directory")
              and not ln.strip().endswith("%")]
    return "\n".join(useful[-keep:])


def package_project(sdk: str, project_dir: str, platforms: list[str],
                    destination: Optional[str] = None,
                    log=print, archive_rpa: bool = False) -> dict:
    """调用官方 SDK 打发布包。platforms: pc/win/linux/mac/android 的组合。

    archive_rpa=True 时先注入官方归档配置，让打包产物里的资源封入 main.rpa。
    """
    # 平台校验必须最先做：空/未知平台在此抛 ValueError，不产生任何
    # 目录或文件副作用（测试与调用方据此保证“失败不建工作目录”）。
    platforms = validate_platforms(platforms)
    sdk = str(Path(sdk).resolve())
    project_dir = str(Path(project_dir).resolve())
    renpy_exe = sdk_mod.require_launcher(sdk)

    dest = str(Path(destination).resolve()) if destination \
        else str(Path(project_dir).parent / "dist-output")
    Path(dest).mkdir(parents=True, exist_ok=True)
    built: list[str] = []
    errors: list[str] = []

    # 审核修复：注入的归档配置只用一次，打包命令跑完后（无论成败）
    # 必须删掉，不能永久留在用户工程里。注意清理在 try 块内、
    # SDK 命令执行完之后（finally 语义），打包过程中引擎能读到它。
    injected = False
    if archive_rpa:
        cfg, backed_up = inject_archive_config(project_dir)
        injected = True
        log(f"已注入 RPA 归档配置：{cfg}")
        if backed_up:
            log(f"警告：检测到已有的同名用户文件 {ARCHIVE_CONFIG_NAME}，"
                f"已先备份为 {ARCHIVE_CONFIG_NAME}{USER_BACKUP_SUFFIX}；"
                "打包结束后只删除本工具注入的配置，"
                "你的备份会保留，可自行核对还原。")

    # 审核修复：产物清单只报本次新出现（或被更新）的文件，
    # 打包前快照目的目录的文件名与修改时间，避免把上次残留的旧包也算进来。
    dest_p = Path(dest)
    before: dict[str, float] = {
        f.name: f.stat().st_mtime for f in dest_p.iterdir() if f.is_file()
    }
    pack_started = time.time()

    try:
        pc_platforms = [p for p in platforms if p in PLATFORM_PACKAGES]
        if pc_platforms:
            cmd = [renpy_exe, "launcher", "distribute", project_dir,
                   "--destination", dest]
            for p in pc_platforms:
                cmd += ["--package", PLATFORM_PACKAGES[p]]
            log(f"执行官方打包：{' '.join(cmd)}")
            # 审核修复：PC/Mac 打包也得有超时（以前无超时，官方启动器
            # 卡死时流水线永远挂着），口径与安卓分支一致
            try:
                proc = run_quiet(cmd, capture_output=True, text=False,
                                 timeout=3600, cwd=sdk)
                out = proc.stdout.decode("utf-8", "replace")
                err = proc.stderr.decode("utf-8", "replace")
                if proc.returncode == 0:
                    for p in pc_platforms:
                        built.append(p)
                    log(out[-2000:] if out else "打包完成")
                else:
                    errors.append(
                        f"PC/Mac 打包失败（退出码 {proc.returncode}）：\n"
                        f"{(err or out)[-3000:]}")
                    log(errors[-1])
            except subprocess.TimeoutExpired:
                errors.append("PC/Mac 打包超时（超过 1 小时），"
                              "请检查机器性能与 SDK 状态。")
                log(errors[-1])

        if "android" in platforms:
            cmd = [renpy_exe, "launcher", "android_build", project_dir,
                   "--destination", dest]
            log(f"执行安卓打包：{' '.join(cmd)}")
            try:
                proc = run_quiet(cmd, capture_output=True, text=False,
                                 timeout=3600, cwd=sdk)
                out = proc.stdout.decode("utf-8", "replace")
                err = proc.stderr.decode("utf-8", "replace")
                if proc.returncode == 0:
                    built.append("android")
                    log(out[-2000:] if out else "安卓打包完成")
                else:
                    errors.append(
                        "安卓打包失败。真实报错（已过滤刷屏进度）：\n"
                        + _clean_build_log((err or out))
                        + "\n常见原因：首次使用需先在 Ren'Py 启动器的"
                          "『偏好设置→安卓』里安装安卓 SDK 并生成密钥，"
                          "然后重试。")
                    log(errors[-1])
            except subprocess.TimeoutExpired:
                errors.append("安卓打包超时（超过 1 小时），"
                              "请检查机器性能与安卓 SDK 状态。")
    finally:
        # 审核修复：无论打包成功还是失败，注入的归档配置都要删掉，
        # 此时 SDK 打包命令已执行完毕，清理不影响产物。
        if injected:
            remove_archive_config(project_dir)
            log("已删除本次注入的 RPA 归档配置，用户工程恢复原状。")

    # 收集产物清单：只报本次打包新出现（或修改时间更新）的文件，
    # 上次残留的旧包不再计入。
    artifacts = []
    if dest_p.exists():
        for f in sorted(dest_p.iterdir()):
            # v0.18.0：Linux 官方发布包是 .tar.bz2（部分平台给 .tgz/.tar.gz），
            # 旧版只认 .zip/.bz2 名字，Linux 产物会不计数。按最长后缀认。
            if f.is_file() and (
                    archives_mod.archive_suffix(f.name)
                    in (".zip", ".tar.bz2", ".tbz2", ".tgz", ".tar.gz",
                        ".tar.xz", ".tar", ".apk", ".aab", ".dmg")):
                stat = f.stat()
                old_mtime = before.get(f.name)
                # 新文件必报；同名文件只有 mtime 变大（或落在本次打包时间窗内，
                # 防文件系统时间戳精度不足）才算本次更新。
                if old_mtime is None or stat.st_mtime > old_mtime \
                        or stat.st_mtime >= pack_started:
                    artifacts.append({"name": f.name, "size": stat.st_size})

    return {"built": built, "errors": errors, "destination": dest,
            "artifacts": artifacts}
