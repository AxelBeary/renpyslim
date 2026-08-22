"""官方 SDK 打包调度：所有打包都委托给 Ren'Py 官方程序，绝不自制。

已核实的官方命令（Ren'Py 8.x，见 SDK launcher/game/distribute.rpy 与 android.rpy）：
- PC/Mac 发布包：  renpy.exe launcher distribute <工程路径> [--package 名] [--destination 目录]
- Android APK：   renpy.exe launcher android_build <工程路径> [--destination 目录]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from .procutil import run_quiet

CONFIG_DIR = Path.home() / ".renpytools"
CONFIG_FILE = CONFIG_DIR / "config.json"

# 界面上的平台选项 -> 官方 distribute 的 --package 名称
PLATFORM_PACKAGES = {
    "pc": "pc",        # Windows + Linux 合包
    "win": "win",
    "linux": "linux",
    "mac": "mac",
}

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
    if explicit and (Path(explicit) / "renpy.exe").exists():
        _remember_sdk(explicit)
        return explicit

    cfg = load_config()
    remembered = cfg.get("sdk_path")
    if remembered and (Path(remembered) / "renpy.exe").exists():
        return remembered

    guesses = [
        Path.home() / "renpy",
        Path("C:/renpy"), Path("D:/renpy"), Path("E:/renpy"),
        Path.home() / "Desktop" / "renpy",
    ]
    for g in guesses:
        if (g / "renpy.exe").exists():
            _remember_sdk(str(g))
            return str(g)
    return None


def _remember_sdk(path: str) -> None:
    cfg = load_config()
    cfg["sdk_path"] = path
    save_config(cfg)


def sdk_version(sdk: str) -> str:
    try:
        out = subprocess.run([str(Path(sdk) / "renpy.exe"), "--version"],
                             capture_output=True, timeout=60)
        return out.stdout.decode("utf-8", "replace").strip().splitlines()[0]
    except Exception:
        return "未知版本"


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
    sdk = str(Path(sdk).resolve())
    project_dir = str(Path(project_dir).resolve())
    renpy_exe = str(Path(sdk) / "renpy.exe")
    if not Path(renpy_exe).exists():
        raise FileNotFoundError(f"SDK 里找不到 renpy.exe：{renpy_exe}")

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
            if f.is_file() and f.suffix in (".zip", ".bz2", ".apk", ".aab", ".dmg"):
                stat = f.stat()
                old_mtime = before.get(f.name)
                # 新文件必报；同名文件只有 mtime 变大（或落在本次打包时间窗内，
                # 防文件系统时间戳精度不足）才算本次更新。
                if old_mtime is None or stat.st_mtime > old_mtime \
                        or stat.st_mtime >= pack_started:
                    artifacts.append({"name": f.name, "size": stat.st_size})

    return {"built": built, "errors": errors, "destination": dest,
            "artifacts": artifacts}
