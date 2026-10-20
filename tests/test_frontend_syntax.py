"""前端三件套的静态闸：JS 语法与 index.html 的拆分引用。

Node 是可选工具（终端用户与 CI 都不需要它跑游戏），
缺失时跳过；存在时必须通过 node --check，防止拆分后的语法漂移静默上线。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "web" / "static"


@pytest.mark.parametrize("name", ["i18n.js", "app.js"])
def test_js_syntax(name):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH，跳过 JS 语法检查")
    path = STATIC / name
    r = subprocess.run([node, "--check", str(path)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"{name} 语法错误：{r.stderr}"


def test_index_links_split_assets():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/styles.css">' in html
    assert '<script src="/i18n.js"></script>' in html
    assert '<script src="/app.js"></script>' in html
    assert html.index('/i18n.js') < html.index('/app.js'), "app.js 必须在 i18n.js 之后加载"
