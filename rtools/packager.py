"""官方 SDK 打包调度：所有打包都委托给 Ren'Py 官方程序，绝不自制。

已核实的官方命令（Ren'Py 8.x，见 SDK launcher/game/distribute.rpy 与 android.rpy）：
- PC/Mac 发布包：  renpy.exe launcher distribute <工程路径> [--package 名] [--destination 目录]
- Android APK：   renpy.exe launcher android_build <工程路径> [--destination 目录]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
_ARCHIVED_PATTERNS = (
    "**.png", "**.jpg", "**.jpeg", "**.webp", "**.bmp",
    "**.ogg", "**.mp3", "**.opus", "**.wav", "**.flac",
    "**.mp4", "**.webm", "**.ogv",
    "**.ttf", "**.otf",
)


def inject_archive_config(project_dir: str) -> str:
    """在工程里写入官方归档配置，打包时资源会自动封入 main.rpa。

    只封资源类文件；脚本、配置一律留在包外，不影响任何运行逻辑。
    重复注入时先删旧文件，避免叠加。
    """
    cfg_path = Path(project_dir) / "game" / ARCHIVE_CONFIG_NAME
    lines = ["# 由 Ren'Py 工具箱自动生成：打包时把资源封入 main.rpa",
             "init python:",
             '    build.archive("main", "all")']
    for pat in _ARCHIVED_PATTERNS:
        lines.append(f'    build.classify("game/{pat}", "main")')
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(cfg_path)


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

    if archive_rpa:
        cfg = inject_archive_config(project_dir)
        log(f"已注入 RPA 归档配置：{cfg}")

    dest = str(Path(destination).resolve()) if destination \
        else str(Path(project_dir).parent / "dist-output")
    Path(dest).mkdir(parents=True, exist_ok=True)
    built: list[str] = []
    errors: list[str] = []

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
                errors.append(f"PC/Mac 打包失败（退出码 {proc.returncode}）：\n"
                              f"{(err or out)[-3000:]}")
                log(errors[-1])
        except subprocess.TimeoutExpired:
            errors.append("PC/Mac 打包超时（超过 1 小时），请检查机器性能与 SDK 状态。")
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
                    + "\n常见原因：首次使用需先在 Ren'Py 启动器的『偏好设置→安卓』里"
                      "安装安卓 SDK 并生成密钥，然后重试。")
                log(errors[-1])
        except subprocess.TimeoutExpired:
            errors.append("安卓打包超时（超过 1 小时），请检查机器性能与安卓 SDK 状态。")

    # 收集产物清单
    artifacts = []
    dest_p = Path(dest)
    if dest_p.exists():
        for f in sorted(dest_p.iterdir()):
            if f.is_file() and f.suffix in (".zip", ".bz2", ".apk", ".aab", ".dmg"):
                artifacts.append({"name": f.name, "size": f.stat().st_size})

    return {"built": built, "errors": errors, "destination": dest,
            "artifacts": artifacts}
