"""根 conftest - 仓库此前无测试目录，按 memory-system-plan.md 第十章要求落地。

把 backend/ 加入 sys.path，使测试可以直接 import services.xxx / models.xxx。
"""
import sys
from pathlib import Path

# backend/ 目录即 conftest.py 的父目录
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
