# Third-Party Notices / 第三方声明

RenPySlim is licensed under **AGPL-3.0** (see [LICENSE](LICENSE)).
This file lists the third-party components the project uses, their licenses,
and how they are distributed. 中文摘要见每节末尾。

## 1. Bundled Python dependencies / 随程序打包的 Python 依赖

These libraries are bundled inside the distributed executable
(built with PyInstaller). 这些库被打包进发布的 exe。

| Package / 包 | Version used / 使用版本 | License / 协议 | Notes / 说明 |
|---|---|---|---|
| [Pillow](https://github.com/python-pillow/Pillow) | 12.x | HPND (PIL License) | Image processing / 图片处理 |
| [fontTools](https://github.com/fonttools/fonttools) | 4.x | MIT | Font subsetting & inspection / 字体瘦身与检测 |
| [FastAPI](https://github.com/fastapi/fastapi) | 0.14x | MIT | Local web UI backend / 本机界面后端 |
| [uvicorn](https://github.com/encode/uvicorn) | 0.5x | BSD-3-Clause | ASGI server (127.0.0.1 only) / 本机服务 |
| [py7zr](https://github.com/miurahr/py7zr) | 1.x | MIT | 7z archive support / 7z 压缩包支持 |
| [pystray](https://github.com/moses-palmer/pystray) | 0.19.x | **LGPL-3.0** | System tray icon / 系统托盘图标 |
| pydantic, starlette, anyio, h11, click, etc. | — | MIT / BSD-3-Clause | Transitive deps of FastAPI/uvicorn / 传递依赖 |

**pystray (LGPL-3.0) compliance / 合规说明**: pystray is a separately
importable library; its source is available from the project above, and
this repository provides the complete corresponding source and build
scripts (`build_exe.bat`) so recipients can relink a modified pystray.
pystray 以独立库形式被调用；按 LGPL 要求，其源码可从上方链接获取，
本仓库同时提供完整应用源码与重建脚本，允许用户替换 pystray 后重新打包。

## 2. Vendored source / 内嵌第三方源码

| Component / 组件 | License / 协议 | Notes / 说明 |
|---|---|---|
| [unrpyc](https://github.com/CensoredUsername/unrpyc) | MIT | Ren'Py script decompiler, vendored at
`rtools/vendor/unrpyc/` (full license text in that directory). Used by the
optional experimental "decompile scripts" feature. Ren'Py 脚本反编译器，
源码内嵌于 `rtools/vendor/unrpyc/`（完整许可证文本见该目录），
仅用于可选的实验性"反编译脚本"功能。 |

## 3. Build & test tools / 构建与测试工具（不随产品分发）

| Tool / 工具 | License / 协议 | Notes / 说明 |
|---|---|---|
| [PyInstaller](https://pyinstaller.org) | GPLv2+ **with bootloader exception** | The exception explicitly permits distributing the
generated executable under any license, including AGPL here. 其 bootloader
例外条款明确允许以任意协议分发生成的 exe。 |
| [pytest](https://pytest.org) | MIT | Tests only / 仅测试用 |

## 4. External programs invoked at runtime / 运行时调用的外部程序（不打包、不分发）

RenPySlim does **not** bundle or distribute any of these. They are looked up
on the user's own machine, and the user installs them independently, so their
licenses do not apply to RenPySlim's distribution.
以下程序**不会被本工具打包或分发**，仅在用户自己的电脑上按需查找调用，
由用户自行安装，其协议与本项目的分发无关。

| Program / 程序 | License / 协议 | How it's used / 用途 |
|---|---|---|
| [FFmpeg](https://ffmpeg.org) | LGPL-2.1+ (or GPL, depending on build) | Audio/video re-encoding / 音视频转码 |
| [7-Zip](https://www.7-zip.org) | LGPL-2.1+ (optional) | Only if user installed it; py7zr handles most 7z natively / 可选 |
| [Ren'Py SDK](https://www.renpy.org) | MIT | Packaging & rpyc compilation, delegated to the official launcher / 打包与脚本编译 |
| Android SDK build-tools (`zipalign`, `apksigner`), JDK `keytool` | Apache-2.0 / GPL+CE (OpenJDK) | APK alignment, signing, keystore generation / APK 对齐、签名、钥匙生成 |

## 5. File-format implementations / 文件格式参考实现

- **RPA archive format & rpyc compiled-script layout**: RenPySlim's pure-Python
  reader/writer (`rtools/rpa.py`, `rtools/charset.py`) was written by studying
  the format used by the [Ren'Py](https://www.renpy.org) engine
  (Copyright Tom Rothamel et al., MIT license; source:
  https://github.com/renpy/renpy). No Ren'Py code is copied into this project;
  only the documented/public format is reimplemented.
  RPA 封包与 rpyc 编译脚本格式：本项目按 Ren'Py 引擎（MIT 协议）公开的行为
  自行实现读写，未复制任何 Ren'Py 源码，特此致谢。
- **APK / ZIP containers** are handled through Python's standard library
  `zipfile` and Android's official tooling listed above.
  APK/ZIP 容器通过 Python 标准库与上方列出的官方工具处理。

## 6. Assets / 素材

- The application icon is an original asset provided by the project author.
  应用图标为项目作者提供的原创素材。
- The web UI (`web/static/index.html`) is fully hand-written with zero
  external CSS/JS dependencies and no network resources.
  网页界面为纯手写单文件，零外部依赖，不加载任何网络资源。

---

If you believe any attribution is missing or incorrect, please open an issue.
如认为上述声明有遗漏或错误，请提交 issue 指正。
