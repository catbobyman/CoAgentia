# golden/ —— 跨语言金标判例（00 §4.4）

指纹（契约 A §2）与 M6 校验器的判例集：Python 权威实现与 TS 镜像必须逐判例一致，CI 双跑。M1 仅指纹判例（`fingerprint.json`）；校验器判例随 M6 kernel 补齐。

- `fingerprint.json` —— 规范化序列化与 SHA-256 指纹（契约 A §2）。
- `graph.json` —— 画布图内核（M3b）：`detect_cycle` 环检测（空图/DAG/自环/2·3·间接环/菱形无环）与 `derive_blocked` 阻塞派生（线性链级联/菱形汇聚/全 satisfied→空集）。
