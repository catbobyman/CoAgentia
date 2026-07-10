"""coagentia-server：M1 后端骨架（契约 A 建表 + 服务层占位）。

模块布局冻结于 00 §3：db/（引擎 + 模型 + 迁移 + 种子）·
ledger/ messages/ tasks/ canvas/ agents/ computers/ orchestration/（M1 空占位）。
形状唯一源 = packages/contracts；SQLAlchemy 模型 import 其枚举，不重复定义字面量（契约 A §8.1）。
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
