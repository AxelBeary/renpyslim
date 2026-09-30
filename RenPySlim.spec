# -*- mode: python ; coding: utf-8 -*-
# exe 构建配方唯一事实源：release.yml 与 build_exe.bat 均以本文件调用 PyInstaller，
# 改打包参数只改这里，勿在调用方重复内联。


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('web/static', 'web/static'), ('assets', 'assets'), ('rtools/vendor/unrpyc', 'rtools/vendor/unrpyc')],
    hiddenimports=['rtools.pipeline', 'rtools.packager', 'pystray._win32'],
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
