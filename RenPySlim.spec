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
