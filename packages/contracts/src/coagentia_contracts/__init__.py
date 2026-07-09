"""coagentia-contracts：唯一契约源（Pydantic-first，00 §4.4）。

模块 ↔ 契约文档对应：
- entities ↔ 契约 A（01-实体表与数据模型）
- rest     ↔ 契约 B（02-REST-API契约）
- ws       ↔ 契约 C（03-WS事件协议）
- daemon   ↔ 契约 D（04-daemon-server协议）
- constants/enums ↔ 各契约常量与枚举（含契约 E 的活动文案/禁用工具）
- kernel   ↔ 契约 A §2 指纹规范（Python 权威实现；TS 镜像以 golden 判例验收）
"""

from coagentia_contracts import constants, daemon, entities, enums, ids, rest, ws
from coagentia_contracts.kernel import fingerprint

__all__ = ["constants", "daemon", "entities", "enums", "fingerprint", "ids", "rest", "ws"]
