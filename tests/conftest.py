"""pytest 统一配置：把项目根目录加入 sys.path。

所有测试直接 `from rtools import ...` 即可，无需各自插 sys.path。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
