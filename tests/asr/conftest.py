"""为同名测试目录与服务包提供统一的导入入口。"""

import importlib
import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
ROOT_DIR = TEST_DIR.parents[1]
sys.path.insert(0, str(ROOT_DIR))
module = sys.modules.get("asr")
if module is not None and Path(module.__file__).resolve().parent == TEST_DIR:
    del sys.modules["asr"]
module = importlib.import_module("asr")
# pytest 默认把这里的测试命名为 asr.test_*，仅在测试期间扩展搜索路径。
if str(TEST_DIR) not in module.__path__:
    module.__path__.append(str(TEST_DIR))
