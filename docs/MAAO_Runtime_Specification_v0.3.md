# MAAO Runtime Specification v0.3

## Model-Aware Adaptive Agent Organization

### 模型感知型自适应智能体组织运行时规范

**Version:** v0.3  
**Status:** Experimental / Reference Implementation  
**Reference Runtime:** JOJO Director  
**Experiment:** E1 — DALI / LF13 / LED  
**Date:** 2026-07

---

# 0. 摘要

MAAO（Model-Aware Adaptive Agent Organization）是一种面向目标驱动智能系统的动态组织运行时。

其核心思想不是：

> 预先设计一组 Agent，然后让 Agent 协作完成任务。

而是：

> **系统首先理解当前基础模型的能力边界，再根据任务需求、失败风险、工具能力、记忆状态和环境约束，动态决定应该形成什么样的 Agent 组织。**

基本运行逻辑：

```text
Goal
  ↓
Task Graph
  ↓
Model Self-Model
  ↓
Capability Map
  ↓
Failure Map
  ↓
Task–Capability Matching
  ↓
Dynamic Agent Formation
  ↓
Execution
  ↓
Verification
  ↓
Feedback
  ↓
Model Self-Model Update
  ↓
Dynamic Reformation
```

MAAO 不把 Agent 视为固定的软件角色，而把 Agent 视为：

> **由目标、模型能力和环境约束动态形成的执行组织。**

因此：

```text
Agent ≠ Fixed Role
Agent = Runtime Organization
```

---

# 1. 设计目标

MAAO v0.3 解决五个核心问题：

### Q1：模型到底知道自己能做什么吗？

通过 **Model Self-Model** 解决。

### Q2：模型的能力如何被机器读取？

通过 **Capability Map** 解决。

### Q3：模型如何知道自己什么时候会失败？

通过 **Failure Map** 解决。

### Q4：一个任务应该交给什么执行路径？

通过 **Task–Capability Matching** 解决。

### Q5：系统应该形成一个 Agent，还是多个 Agent？

通过 **Dynamic Agent Formation Policy** 解决。

---

# 2. 核心设计原则

## P1. Model First

系统在形成 Agent 组织之前，必须先理解基础模型能力。

```text
Model Understanding
        ↓
Organization Formation
```

而不是：

```text
Organization Formation
        ↓
Force Model to Execute
```

## P2. Capability Is Conditional

能力不是模型的静态标签。

不是：

```text
Model A = Good at Images
```

而是：

```text
Model
×
Task
×
Context
×
Constraint
×
Tool
×
Verification
```

共同决定当前任务成功的概率。

## P3. Failure Is First-Class Data

失败不是异常日志。

失败本身就是 **Model Understanding** 的核心数据。

## P4. Organization Is Adaptive

Agent 数量、角色、拓扑、工具和模型均可动态变化。

## P5. Self-Modification ≠ Self-Authorization

Agent 可以：

- 重组
- 拆分
- 合并
- 更换模型
- 更换工具

但不能自行提升权限。

权限始终由：

> Human / Policy / Runtime

控制。

---

# 3. Runtime 总体架构

```text
┌──────────────────────────────────────────────┐
│                    HUMAN                     │
│       Goal / Value / Authority / Override    │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│                TASK MODELING                 │
│       Goal → Task Graph → Constraints        │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│              MODEL SELF-MODEL                │
│   当前模型能力 / 可靠性 / 成本 / 失败边界     │
└──────────────────────┬───────────────────────┘
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
┌────────────────────┐ ┌─────────────────────┐
│  CAPABILITY MAP    │ │    FAILURE MAP      │
│ 我能做什么？       │ │ 我在哪里会失败？    │
└─────────┬──────────┘ └──────────┬──────────┘
          └────────────┬──────────┘
                       ▼
          TASK–CAPABILITY MATCHING
                       │
                       ▼
          DYNAMIC FORMATION POLICY
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
          Single    Multi      Human
          Agent     Agent      Loop
             │         │         │
             └─────────┼─────────┘
                       ▼
                    EXECUTE
                       │
       ┌───────────────┼────────────────┐
       ▼               ▼                ▼
    Memory           Tool          Environment
       │               │                │
       └───────────────┼────────────────┘
                       ▼
                   VERIFY / QC
                       │
               ┌───────┴───────┐
               ▼               ▼
            SUCCESS          FAILURE
               │               │
               │               ▼
               │        FAILURE DIAGNOSIS
               │               │
               └───────┬───────┘
                       ▼
                    FEEDBACK
                       │
                       ▼
               SELF-MODEL UPDATE
                       │
                       ▼
                REFORMATION
                       │
                       └───────────────↺
```

---

# 4. 五个核心 JSON Schema

MAAO v0.3 定义五个核心数据对象：

```text
01 ModelSelfModel
02 CapabilityMap
03 FailureMap
04 TaskGraph
05 AgentOrganization
```

---

# 4.1 ModelSelfModel

## 目的

描述：

> MAAO 当前“认为自己所使用的基础模型能做什么，以及在什么条件下可靠”。

它不是模型厂商提供的 Model Card，而是：

> **Runtime-generated Model Understanding。**

## Schema

```json
{
  "model_id": "string",
  "model_version": "string",
  "identity": {
    "provider": "string",
    "modality": [
      "text",
      "image",
      "video",
      "code"
    ]
  },
  "capability_profiles": {
    "capability_id": {
      "base_score": 0.0,
      "reliability": 0.0,
      "confidence": 0.0,
      "evidence_count": 0
    }
  },
  "known_failure_modes": [
    "string"
  ],
  "context_affinity": {},
  "tool_affinity": {},
  "environment_affinity": {},
  "cost_profile": {},
  "latency_profile": {},
  "last_updated": "datetime"
}
```

### 关键字段

- `base_score`：模型理论上的能力估计。
- `reliability`：真实任务中稳定成功的概率。
- `confidence`：MAAO 对上述判断的可信程度。
- `evidence_count`：支持该结论的真实实验数量。

---

# 4.2 CapabilityMap

Capability Map 是 Model Self-Model 的细粒度能力层。

核心定义：

```text
Capability =
Model × Task × Context × Constraint
```

## Schema

```json
{
  "capability_id": "string",
  "model_id": "string",
  "task_type": "string",
  "requirements": [
    "string"
  ],
  "score": 0.0,
  "reliability": 0.0,
  "confidence": 0.0,
  "cost": 0.0,
  "latency": 0.0,
  "failure_risk": 0.0,
  "recommended": true,
  "alternative_routes": [
    {
      "route": "string",
      "score": 0.0
    }
  ],
  "evidence": [
    "experiment_id"
  ]
}
```

## JOJO 实证映射

JOJO 已经真实验证出以下能力边界：

```text
文生图
├── 连接拓扑 → 低可靠
├── 严格空间关系 → 低可靠
└── 精确数量 → 不稳定

编辑模型
├── 亮度方向变化 → 低可靠
├── 颜色方向变化 → 低可靠
└── 屏幕小字保持 → 低可靠

Code Render
├── 精确工程结构 → 高可靠
├── 四步循环 → 高可靠
└── 安全联锁 → 高可靠

视频模型
├── 氛围变化 → 更适合
├── 亮度变化 → 更适合
└── 动态过程 → 更适合
```

这些能力边界是 JOJO 项目实测结果在 MAAO 中的结构化抽象。

---

# 4.3 FailureMap

Failure Map 描述：

> 系统如何失败。

## Schema

```json
{
  "failure_id": "string",
  "capability_id": "string",
  "task_id": "string",
  "trigger": {
    "conditions": []
  },
  "observed_failure": {
    "description": "string",
    "frequency": 0.0
  },
  "root_cause": [
    "string"
  ],
  "impact": "low|medium|high|critical",
  "detection": [
    "string"
  ],
  "recovery": [
    {
      "action": "string",
      "priority": 1
    }
  ],
  "prevention": [
    "string"
  ],
  "stop_rule": {
    "max_retry": 2,
    "action_after_limit": "string"
  }
}
```

## JOJO 实证映射

例如：

```text
Failure:
严格空间拓扑失败

Trigger:
line must physically connect to device

Observed:
连续两次生成仍然悬空/错接

Root Cause:
generative spatial instability

Recovery:
1. Human Override
2. Switch to Single Frame
3. Switch to Code Render

Stop Rule:
2 次失败后停止继续抽卡
```

这直接对应 JOJO 已有的失败止损思想，并在 MAAO 中抽象为 Failure Recovery Policy。

---

# 4.4 TaskGraph

TaskGraph 是 Goal 到可执行任务的中间层。

## Schema

```json
{
  "task_graph_id": "string",
  "goal": "string",
  "constraints": {
    "accuracy": 0.0,
    "consistency": 0.0,
    "cost_limit": 0.0,
    "time_limit": 0.0
  },
  "tasks": [
    {
      "task_id": "string",
      "type": "string",
      "requirements": [
        "string"
      ],
      "capabilities_required": [
        {
          "capability": "string",
          "weight": 0.0
        }
      ],
      "verification": {
        "required": true,
        "method": "string"
      },
      "dependencies": []
    }
  ]
}
```

---

# 4.5 AgentOrganization

这是动态形成后的组织结果。

## Schema

```json
{
  "organization_id": "string",
  "goal": "string",
  "formation_reason": [
    "string"
  ],
  "agents": [
    {
      "agent_id": "string",
      "role": "string",
      "model_id": "string",
      "capabilities": [
        "string"
      ],
      "tools": [
        "string"
      ],
      "authority": [
        "read",
        "write"
      ]
    }
  ],
  "topology": {
    "type": "single|sequential|parallel|hierarchical|adaptive",
    "edges": []
  },
  "routing_rules": [],
  "verification_policy": {},
  "reformation_policy": {}
}
```

---

# 5. 六个 Runtime API

## API 1：`probe_model()`

主动测试基础模型能力。

```text
probe_model()
        ↓
Controlled Task
        ↓
Execute
        ↓
Verify
        ↓
Capability Evidence
```

例如：

```json
{
  "probe": "strict_spatial_topology",
  "task": "connect_pipe_to_device",
  "verification": "visual_qc"
}
```

结果：

```json
{
  "status": "FAIL",
  "reliability_update": -0.12
}
```

---

## API 2：`update_capability()`

```text
update_capability(
    task,
    result,
    verification,
    context
)
```

更新：

```text
Capability Score
Reliability
Confidence
Failure Risk
```

---

## API 3：`record_failure()`

```text
record_failure(
    task,
    observed_result,
    evidence,
    diagnosis
)
```

生成：

```text
Failure Map Entry
```

---

## API 4：`match_task()`

这是 MAAO 的决策核心。

```text
match_task(
    TaskGraph,
    ModelSelfModel,
    CapabilityMap,
    FailureMap
)
```

输出：

```text
Best Route
Alternative Routes
Risk
Expected Cost
Expected Success
```

---

## API 5：`form_organization()`

```text
form_organization(
    TaskGraph,
    CapabilityMap,
    FailureMap,
    Tools,
    Memory,
    Constraints
)
```

输出：

```text
AgentOrganization
```

---

## API 6：`reform_organization()`

```text
reform_organization(
    current_organization,
    failure_evidence,
    updated_capability_map,
    updated_failure_map
)
```

可能产生：

```text
Agent Split
Agent Merge
Agent Retire
Agent Create
Model Switch
Tool Add
Tool Remove
Human Escalation
```

---

# 6. Dynamic Agent Formation Policy

MAAO 不追求：

> Agent 越多越智能。

而追求：

> **在满足成功率的前提下，寻找最小有效组织。**

可定义目标函数：

```text
Organization Utility
=
Success Probability
-
Cost
-
Latency
-
Risk
-
Coordination Complexity
```

因此：

```text
Single Agent
        ↓
Capability Insufficient?
        ↓
Tool Augmentation
        ↓
Still Insufficient?
        ↓
Task Decomposition
        ↓
Still Risky?
        ↓
Reviewer / Verifier
        ↓
Still Impossible?
        ↓
Model Switch
        ↓
Still Impossible?
        ↓
Human
```

---

# 7. E1 实验：JOJO Director

MAAO v0.3 的第一轮实验不采用虚构 Benchmark。

直接使用：

> **JOJO Director 已经真实完成的 DALI、LF13、LED 三个项目。**

三个项目分别代表三个不同的 MAAO 问题：

| 案例 | 主要验证问题 |
|---|---|
| DALI | 动态路由 + 首尾帧一致性 + Failure Recovery |
| LF13 | AI / Code Render 能力边界 |
| LED | 大规模任务拆解 + 模型能力边界 + Human Override |

---

# E1-A：DALI

## 实际项目事实

DALI 三镜完整闭环：

```text
镜1
单帧动效
↓
放行

镜2
PWM Waveform Code Render
↓
放行

镜3
统一扁平世界
↓
插值
↓
Skip Pair Check
↓
放行

Compose
↓
35.1 秒
```

项目真实经历了：

- 首尾帧不一致
- 歧义词 bus 导致错误生成
- 严格空间连接关系失败
- 视觉裁判误判
- 人工终裁
- 单帧模式切换
- Code Render 路由
- 配对预检

这些内容在 MAAO 中分别对应：

```text
Failure Map
Task–Capability Matching
Human Override
Route Switching
Pair Verification
Recovery Policy
```

## MAAO 重构

### TaskGraph

```text
DALI Goal
│
├── Shot 1
│   └── Light Pulse
│
├── Shot 2
│   └── PWM Waveform
│
└── Shot 3
    └── Shape Transition
```

### Capability Matching

```text
Shot 1

Image Generation
        ↓
Strict Topology Risk
        ↓
HIGH
        ↓
Single Frame Video
```

```text
Shot 2

Visual AI
        ↓
Exact Waveform
        ↓
Low Reliability

Code Render
        ↓
High Reliability
        ↓
Select Code Render
```

```text
Shot 3

Morphological Transition
        ↓
Need First/Last Frame
        ↓
Pair Consistency Required
        ↓
Pair Precheck
```

### Dynamic Organization

```text
Director Agent
│
├── Shot Planner
├── Image Agent
├── Code Render Agent
├── Video Agent
├── Pair QC Agent
└── Final QC Agent
```

这不是预先固定的，而是：

```text
Task Requirement
+
Capability Map
+
Failure Map
```

共同决定的动态组织结果。

---

# E1-B：LF13

LF13 是 MAAO 最重要的一个案例。

因为它证明：

> **同一个项目中，不同镜头需要不同的生产范式。**

实际成片：

```text
镜1
AI 车间实景

镜2
参考图锚定轴测图解

镜3
Code Render
四步循环
28s

镜4
Code Render
安全联锁
26s

镜5
AI 黄昏收尾

Total
84s
```

## MAAO 的关键判断

```text
Shot 1
Visual Realism
↓
AI Image / Video

Shot 2
Reference Anchoring
↓
Image Generation

Shot 3
Exact Process Logic
↓
Code Render

Shot 4
Safety Interlock
↓
Code Render

Shot 5
Atmosphere
↓
AI Video
```

于是：

```text
One Goal
≠
One Agent
```

更准确地说：

```text
One Goal
→
Multiple Task Types
→
Multiple Capability Requirements
→
Multiple Execution Modes
```

这正是 MAAO 的核心。

---

# E1-C：LED

LED 是第三个关键案例。

该项目最终：

```text
12 Shots
=
11 AI Videos
+
1 Code Render
```

总时长：

```text
2m08s
```

项目真实记录还明确给出了模型能力边界：

- 文生图严格连接拓扑成功率低
- 编辑模型亮度/颜色方向变化成功率接近 0
- 编辑模型无法稳定保持屏幕小字
- 图生图精确数量不稳定

并形成了具体的路由规则：

```text
Topology
→ Code Render

Brightness / Color Change
→ Video Model

Screen Text
→ Single Frame Mode

Exact Count
→ Relax Assertion / Human Review
```

## LED 对 MAAO 的最大贡献

它证明：

> **Failure Map 可以直接改变 Agent Organization。**

例如：

```text
Task:
Brightness Transition

Capability:
Edit Model = 0.05

Failure Risk:
HIGH

Alternative:
Video Model = 0.87
```

于是：

```text
Edit Agent
      ↓
Retire

Video Agent
      ↓
Create
```

这就是：

# Dynamic Agent Reformation

---

# 8. E1 实验假设

MAAO E1 应验证四个假设。

### H1

> Model Self-Model 能否提前识别模型能力边界？

### H2

> Capability Map 能否减少错误路由？

### H3

> Failure Map 能否减少无效重试？

### H4

> Dynamic Formation 能否减少人工干预和无效 Agent 协作？

---

# 9. E1 对照实验

建议建立：

```text
Baseline
JOJO Original Runtime

vs.

Experimental
JOJO + MAAO Runtime
```

## Baseline

```text
Goal
↓
Agent Plan
↓
Execute
↓
QC
↓
Failure
↓
Retry
↓
Human Override
```

## MAAO

```text
Goal
↓
TaskGraph
↓
Model Self-Model
↓
Capability Map
↓
Failure Map
↓
Task Matching
↓
Formation
↓
Execute
↓
QC
↓
Feedback
↓
Reformation
```

---

# 10. 实验指标

建议 E1 记录：

```text
1. Task Success Rate
2. QC Reject Rate
3. Average Retry Count
4. Failed Generation Count
5. Wrong Route Count
6. Human Override Count
7. Model Switch Count
8. Agent Count
9. Agent Reformation Count
10. Total Cost
11. Total Latency
12. Final Quality
```

其中最关键的是：

# Failed Attempts Avoided

因为 MAAO 的核心价值不是：

> 最后能不能成功。

而是：

> **系统能否在失败发生之前，意识到当前路径很可能是错的。**

---

# 11. E1 最重要的验证指标：Route Regret

定义：

> **Route Regret 是系统在已经拥有足够模型能力信息之后，仍然选择错误执行路径所造成的浪费。**

例如：

```text
AI Image
↓
Strict Spatial Topology
↓
Known Failure
↓
Retry × 3
```

Route Regret：

```text
HIGH
```

而：

```text
Capability Map
↓
Detect Risk
↓
Code Render
```

Route Regret：

```text
LOW
```

Route Regret 可能比单纯的成功率更能衡量 MAAO 的价值。

---

# 12. MAAO 的真正闭环

```text
                  ┌──────────────┐
                  │    GOAL      │
                  └──────┬───────┘
                         ↓
                  ┌──────────────┐
                  │ TASK GRAPH   │
                  └──────┬───────┘
                         ↓
             ┌────────────────────────┐
             │    MODEL SELF-MODEL    │
             └───────────┬────────────┘
                         ↓
              ┌──────────┴──────────┐
              ↓                     ↓
       CAPABILITY MAP          FAILURE MAP
              │                     │
              └──────────┬──────────┘
                         ↓
                 TASK MATCHING
                         ↓
               AGENT FORMATION
                         ↓
                     EXECUTE
                         ↓
                    VERIFY
                         ↓
              ┌──────────┴──────────┐
              ↓                     ↓
           SUCCESS               FAILURE
              │                     │
              │              DIAGNOSE FAILURE
              │                     │
              └──────────┬──────────┘
                         ↓
                      FEEDBACK
                         ↓
                 UPDATE SELF-MODEL
                         ↓
                  REFORM SYSTEM
                         │
                         └──────────────↺
```

---

# 13. v0.3 最重要的理论升级

结合 JOJO 的三个真实案例，目前可以把 MAAO 的理论核心进一步压缩为：

> **一个 Agent 系统不应该首先问“我应该创建多少个 Agent”，而应该首先问“我当前使用的模型，在这个具体任务中究竟可靠到什么程度”。**

因此：

```text
Model Understanding
        ↓
Capability Understanding
        ↓
Failure Understanding
        ↓
Task Matching
        ↓
Organization Formation
```

而不是：

```text
Goal
↓
Create 10 Agents
↓
Hope They Cooperate
```

这也是将“Model First”思想真正落地到 MAAO 后的核心形式。

---

# 14. v0.3 → v0.4 的下一步

在真正开发 Simulator 之前，还需要一个关键中间层：

# MAAO Runtime State Machine

把：

```text
PROBE
UNDERSTAND
MAP
MATCH
FORM
EXECUTE
VERIFY
FAIL
DIAGNOSE
REFORM
```

正式定义为：

> 有限状态机 + 事件系统。

然后把 JOJO 的真实运行过程映射进去：

```text
DALI
  PROBE → MATCH → FORM → EXECUTE → FAIL → REFORM

LF13
  MATCH → FORM
  AI Image + Code Render + AI Video

LED
  EXECUTE → FAIL
  → Failure Map
  → Model Capability Update
  → Route Switch
```

这样下一阶段的 HTML Simulator 就不再是“动画模拟”，而是：

> **一个可以运行 MAAO Runtime State Machine 的可交互实验环境。**

用户输入：

> “制作一个自动钻孔工作站微课。”

系统实时显示：

```text
[GOAL]

[MODEL SELF-MODEL]
当前模型：
Spatial Topology = LOW
Code Rendering = HIGH
Video Motion = MEDIUM

[CAPABILITY MAP]

[FAILURE MAP]

[TASK GRAPH]

[FORMATION]

Director
 ├── Image Agent
 ├── Code Render Agent
 ├── Video Agent
 └── QC Agent

[EXECUTION]

⚠️ Shot 3
Risk detected:
Strict spatial constraint

[REFORMATION]

Image Agent
       ↓
Code Render Agent

[FINAL ORGANIZATION]
```

最终可以切换不同模型，观察：

> **同一个 Goal，因为 Model Self-Model 不同，最终形成不同的 Agent Organization。**

这才是下一阶段真正值得做的 MAAO Runtime Simulator。

---

# 15. 当前 v0.3 的实验结论

基于 JOJO Director 的三个真实案例，目前可以形成一个非常有价值的初步判断：

> **DALI 证明了 MAAO 需要 Failure-Aware Routing；LF13 证明了 MAAO 需要 Task–Capability Matching；LED 证明了 MAAO 需要 Runtime Reformation。**

三个案例合起来，形成：

```text
DALI
Failure-Aware Routing
        +
LF13
Task–Capability Matching
        +
LED
Dynamic Reformation
        ↓
MAAO Runtime
```

三个案例目前更适合作为：

> **E1 的“历史回放式验证样本”**

而不是已经完成的严格 A/B 实验。

因为现有项目记录保存了真实生产过程和决策，但还没有同一任务在：

```text
Baseline JOJO
```

与：

```text
MAAO Runtime
```

下的严格双跑数据。

因此，下一步最严谨的工作是：

> **用三个项目的原始 TaskGraph、节点路由、QC 记录和失败记录，构建一个 MAAO Replay Engine。**

先离线重放历史决策，再计算：

- 如果提前拥有 Capability Map，可以避免多少错误路由；
- 如果提前拥有 Failure Map，可以避免多少无效重试；
- 如果具备 Dynamic Formation，可以减少多少不必要的 Agent；
- 如果具备 Reformation Policy，可以提前多少次完成路径切换。

这一步完成后，才能真正回答：

> **MAAO 到底是在“讲一个漂亮的 Agent 理论”，还是确实能让 JOJO Director 用更少的错误路径、更少的重试和更少的人力完成同样的生产任务。**

这将是从：

> **v0.3 Runtime Specification**

进入：

> **v0.4 Experimental Runtime**

的真正分界线。

---

# 16. 一页式总览

```text
MAAO
Model-Aware Adaptive Agent Organization
│
├── 1. Model Self-Model
│      └── 我知道模型当前能做什么
│
├── 2. Capability Map
│      └── 我知道在哪些条件下可靠
│
├── 3. Failure Map
│      └── 我知道在哪里会失败
│
├── 4. Task–Capability Matching
│      └── 我知道这个任务应该走哪条路径
│
├── 5. Dynamic Agent Formation
│      └── 我知道应该形成什么组织
│
├── 6. Execution
│      └── Agent / Tool / Memory / Environment
│
├── 7. Verification
│      └── QC / Human / Automated Evaluation
│
├── 8. Feedback
│      └── 更新 Model Self-Model
│
└── 9. Reformation
       └── 根据新证据重新组织 Agent
```

最终核心公式：

```text
Goal
+
Model Understanding
+
Capability Map
+
Failure Map
+
Task Matching
+
Dynamic Formation
+
Feedback
=
Adaptive Agent Organization
```

最终核心问题：

> **不是“我有多少 Agent”，而是“我是否足够了解自己的模型，以至于知道现在应该成为一个什么样的 Agent 组织”。**

---

# 17. 研究路线图

```text
v0.1
Architecture Discussion
        ↓
v0.2
Executable Data Structures
        ↓
v0.3
Runtime Specification
        ↓
        ├── 5 JSON Schemas
        ├── 6 Runtime APIs
        ├── Formation Policy
        └── JOJO E1 Cases
        ↓
v0.3.x
MAAO Replay Engine
        ↓
        ├── DALI Replay
        ├── LF13 Replay
        └── LED Replay
        ↓
v0.4
Experimental Runtime
        ↓
        ├── Runtime State Machine
        ├── Event System
        ├── Capability Learning
        ├── Failure Learning
        └── Dynamic Reformation
        ↓
v0.5
Interactive HTML Simulator
        ↓
v0.6
JOJO Director Integration
        ↓
MAAO
Model-Aware Adaptive Agent Organization
```

---

# 18. 最终定位

MAAO 的最终目标不是：

> 创建一个更复杂的 Agent Framework。

而是：

> **构建一个能够认识自身模型、理解自身能力边界、预测自身失败模式，并根据任务动态重组自身执行组织的 Runtime。**

最终：

```text
传统 Agent System

Human
 ↓
Prompt
 ↓
Agent
 ↓
Tool
 ↓
Result


MAAO

Human
 ↓
Goal
 ↓
Task Understanding
 ↓
Model Self-Model
 ↓
Capability Map
 ↓
Failure Map
 ↓
Task–Capability Matching
 ↓
Dynamic Agent Formation
 ↓
Execution
 ↓
Verification
 ↓
Feedback
 ↓
Self-Model Update
 ↓
Reformation
 ↓
New Organization
```

因此，MAAO 最终形成的不是一个固定的 Agent，而是：

> **一个持续认识自己、持续修正自己、持续重新组织自己的智能系统。**
