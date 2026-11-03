"""使每个测试文件都能独立导入当前工作树中的插件。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# CI 等无 AstrBot 的环境：注入最小桩 astrbot.api.logger——本仓库测试
# 使用日志桩；真实框架装配在独立环境和进程中验证。
try:
    from astrbot.api import logger  # noqa: F401
except ImportError:
    import sys as _sys
    import types as _types

    class _OfflineLogger:
        def __getattr__(self, name):
            return lambda *a, **k: None

    _pkg = _types.ModuleType("astrbot")
    _api = _types.ModuleType("astrbot.api")
    _api.logger = _OfflineLogger()
    _pkg.api = _api
    _sys.modules.setdefault("astrbot", _pkg)
    _sys.modules.setdefault("astrbot.api", _api)
