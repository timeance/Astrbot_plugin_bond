"""使每个测试文件都能独立导入当前工作树中的插件。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
