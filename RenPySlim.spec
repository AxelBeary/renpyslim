# -*- mode: python ; coding: utf-8 -*-
# exe 构建配方唯一事实源：release.yml 与 build_exe.bat 均以本文件调用 PyInstaller，
# 改打包参数只改这里，勿在调用方重复内联。


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('web/static', 'web/static'), ('assets', 'assets'), ('rtools/vendor/unrpyc', 'rtools/vendor/unrpyc')],
    # cli：入口把 --version / --help / 子命令分流给无头 CLI（main.py::_classify_argv），
    # 显式列进 hiddenimports，避免静态分析漏掉导致 exe 里 import 失败。
    hiddenimports=['rtools.pipeline', 'rtools.packager', 'pystray._win32', 'cli'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RenPySlim',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)

# ---------------------------------------------------------------------------
# 控制台版 CLI：RenPySlim-cli.exe
#
# 为什么单出一个而不是让窗口版兼职：图形界面版用 console=False 构建，
# 进程没有自己的控制台。想靠 AttachConsole(ATTACH_PARENT_PROCESS) 借用调用方
# 终端，在 Windows Terminal / ConPTY 下并不可靠（v0.17.3 实测：PowerShell 里
# .\RenPySlim.exe --version 完全无输出）。控制台子系统是唯一在所有调用方
# （PowerShell、cmd、计划任务、CI、管道）都稳定的做法。
#
# 不打包 web 静态资源与 pystray：CLI 用不到，体积也更小。
cli_a = Analysis(
    ['cli.py'],
    pathex=[],
    binaries=[],
    datas=[('rtools/vendor/unrpyc', 'rtools/vendor/unrpyc')],
    hiddenimports=['rtools.pipeline', 'rtools.packager'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
cli_pyz = PYZ(cli_a.pure)

cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    cli_a.binaries,
    cli_a.datas,
    [],
    name='RenPySlim-cli',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
