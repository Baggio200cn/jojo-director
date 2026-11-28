# JOJO 科学 Skill 包

每个目录对应一个可版本化的候选生产包，包含 `manifest.yaml`、`SKILL.md`、`params.yaml`、`spec_template.json` 和 `qc/rules.yaml`。

## 状态

- `candidate`：骨架与来源已登记，未完成科学证据和教研批准，不得用于生产。
- `verified`：证据、验证和完整项目已完成，等待批准。
- `approved`：允许按指定范围生产，仍需当次验收。
- `deprecated`：停止使用，仅供历史追溯。

当前三个骨架用于验证确定性 2D、真实素材和代码执行，均为 `candidate`，没有正式启用的 Skill。

## 结构

`manifest.yaml` 必须与 `backend/schemas/training-skill-manifest-v1.json` 对齐。`qc/rules.yaml` 只放可审核规则，正反样例必须来自真实记录，不得为了凑数编造。
