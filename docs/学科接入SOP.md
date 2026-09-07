# 学科接入 SOP（光学样板沉淀 v1）

> 适用对象：向 JOJO Director 的 QC 体系接入一个新学科（如电磁学、基础医学）或一个新课题（如杨氏双缝、单摆）的人。
> 本文档由迈克尔逊干涉样板（工作流 B）落地过程沉淀，配套代码位置：`backend/qc_rules/`、`backend/app/executors.py`。

---

## 0. 体系结构：四层规则漏斗

QC 提示词按「从通用到具体」分层装载，越具体越贴近当前项目，防止通用规则稀释专项要求：

```
第1层 通用层    general.yaml          任何项目必载（画面可读性、文字拼写、常识错误）
第2层 学科层    optics.yaml 等         按 project.domain 装载（光路、成像、仪器名称）
第3层 课题层    michelson.yaml 等      topic_of 标注学科 + applies_when 门控命中才载
第4层 断言样例  assertion_samples.yaml 真实项目的正/反审核断言，注入提示词做校准
```

装载逻辑集中在 `app/executors.py`：

- `_load_rules(domain)` → 第1+2层，`_topic_packs(domain)` → 第3层
- `_load_assertion_samples(domain, cap=8)` → 第4层（总量封顶 8 条，防提示词膨胀）
- 课题层的 `pitfalls`（历史误判记录）会并入第4层反例

**设计红线：样例总量封顶、课题规则靠门控命中。宁可少载，不可稀释。**

---

## 1. 接入一个新学科（如电磁学）

### 步骤

1. **新建学科规则文件** `qc_rules/<domain>.yaml`，参照 `optics.yaml` 结构：
   ```yaml
   rules:
     - id: ELEC-01
       severity: high            # high / medium / low
       check: |                  # 用自然语言写清"什么情况算违规"
         电路图中电源正负极标注与电流方向矛盾。
   ```
2. **id 命名**：学科拼音/英文缩写 + 两位序号（`ELEC-01`），不与现有 id 冲突。
3. **至少 5 条规则**才认为该学科具备基本 QC 能力；不足时先用通用层顶着。
4. **验证**：`backend/tests/` 中加装载断言（参照 `test_workstream_b.py` 的 B2 组），确认 `domain="elec"` 能载、其他学科不串。

### 验收标准

- [ ] 规则文件存在且 ≥5 条，每条有 id / severity / check
- [ ] `_load_rules("<新学科>")` 返回新规则 + 通用层规则
- [ ] `_load_rules("mechanics")`（等任一其他学科）**不含**新规则
- [ ] 测试全绿

---

## 2. 接入一个新课题（如杨氏双缝）

课题层是**从真实项目复盘而来**，不是拍脑袋写的。流程：

### 2.1 前提

- 该课题已跑过至少 1 个完整项目（参考复刻流程走完一遍）
- 积累了审核断言记录和（最好有）人工改判记录

### 2.2 写课题规则包 `qc_rules/<topic>.yaml`

```yaml
topic_of: optics               # 所属学科，决定挂在哪一层的装载器下
topic_name: 杨氏双缝干涉
vocabulary:                    # 仪器/部件词表，注入提示词约束模型用词
  - 双缝
  - 单缝
  - 观察屏
rules:
  - id: YOUNG-01
    severity: high
    applies_when: "双缝"        # 门控词：项目课题/描述命中才装载，防稀释
    check: |
      条纹间距必须均匀；出现条纹间距渐变视为错误。
pitfalls:                      # 历史误判记录（模型容易错判/漏判的点）
  - id: YOUNG-P01
    kind: negative
    text: "勿将屏上污渍误判为条纹缺陷"
```

### 2.3 门控（applies_when）怎么写

- 用项目课题名/描述中**必然出现的词**（如"迈克尔逊"课题门控词就是"迈克尔逊"）
- 一个词即可，不要写长句；不命中时整个规则包不装载
- 测试必须覆盖「命中装载 / 不命中不装载」两个方向

### 2.4 验收标准

- [ ] `topic_of` 正确，`applies_when` 双向测试通过
- [ ] vocabulary ≥3 个部件词
- [ ] pitfalls ≥1 条（没有真实误判就先空着，**不要编造**）

---

## 3. 沉淀断言样例（第4层）

断言样例是 QC 模型的"校准锚点"：正例告诉它什么是好的断言，反例告诉它什么是误判。

### 3.1 来源纪律

- **正例**：从服务器真实项目的审核断言记录中挑表述规范、判定准确的，**逐条人工确认后**录入 `assertion_samples.yaml`，标注 `domain` 和 `kind: positive`
- **反例**：来自人工改判记录（模型判错、老师改回的），标注 `kind: negative`，写清"为什么这是误判"
- **禁止编造样例**。没有真实积累时该学科样例为空，靠通用反例（`SMP-GEN-*`）顶着

### 3.2 录入格式

```yaml
samples:
  - id: SMP-ELEC-01
    kind: positive
    domain: elec
    text: "画面中可见干电池、开关、小灯泡组成闭合回路"
```

### 3.3 容量纪律

- `_load_assertion_samples(domain, cap=8)`：每学科最多注入 8 条（正例优先，反例补位，课题 pitfalls 并入）
- 样例库总量增长时，**淘汰表述模糊的旧样例**，不是简单累加

---

## 4. 完整接入检查单（新学科从零到可用）

1. [ ] 跑通该学科第一个真实项目，积累审核记录
2. [ ] 写学科层 `<domain>.yaml`（≥5 条规则）
3. [ ] 写课题层 `<topic>.yaml`（门控 + 词表 + pitfalls）
4. [ ] 从真实记录沉淀断言正例 ≥3 条、反例 ≥1 条
5. [ ] 补测试：装载正确性、学科隔离、门控双向、总量封顶
6. [ ] 服务器回归 `tests/regression.py` 3/3 通过
7. [ ] 更新本文档的学科清单

### 当前学科/课题清单

| 层级 | 文件 | 状态 |
|---|---|---|
| 通用 | general.yaml / fail_classes.yaml / prompt_lint.yaml | ✅ 常载 |
| 学科 | optics.yaml | ✅ |
| 学科 | mechanics.yaml / kinematics.yaml / machine_vision.yaml | ✅ 早期已有 |
| 课题 | michelson.yaml（迈克尔逊干涉，topic_of: optics） | ✅ 样板 |
| 断言 | assertion_samples.yaml（SMP-MIC 正例5 + 反例2） | ✅ v1 |
| 待接入 | 基础医学教学类（目录已建，规则未写） | ⬜ 等真实项目积累 |
