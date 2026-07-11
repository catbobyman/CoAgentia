"""模板域（契约 B §11.1/§11.2 / A §4.10；M5b）：画布快照序列化、工程三角 builtin、保存/列表。

- `builtin`：工程三角 builtin 常量（server 侧放这里避免 contracts gen churn；用 contracts
  TemplateBody/TaskPlanBody 构造，形状单源仍在 contracts 包——纪律 7）。
- `service`：canvas 快照 → TemplateBody 序列化、TemplateBody 校验（无环/引用一致性）、工作区级
  列表、builtin 启动 upsert。
实例化事务（POST /templates/{id}/instantiate）归 H6，本域只承载保存与列表。
"""
