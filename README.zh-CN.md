# product-swimlane-drawio

[English](README.md) | [简体中文](README.zh-CN.md)

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-compatible-5B5BD6.svg)](https://agentskills.io/)
[![Claude Plugin](https://img.shields.io/badge/Claude-Plugin%20Marketplace-D97757.svg)](https://code.claude.com/docs/en/plugin-marketplaces)
[![Codex Plugin](https://img.shields.io/badge/Codex-Plugin%20Marketplace-111827.svg)](https://developers.openai.com/codex/)

![product-swimlane-drawio overview](docs/illustrations/product-swimlane-readme/overview-zh.png)

**经得起下一次修改的可编辑产品泳道图。**

`product-swimlane-drawio` 将已确认的产品或业务流程转换为原生 `.drawio` 文件。Agent 负责理解流程语义，确定性本地引擎负责布局、路由、校验和安全的增量修改；用户最近一次在本地保存的 Draw.io 文件始终是后续修改的权威来源。

**语义化 → 确定性 → 可编辑 → 可迭代 → 可验证**

生成过程不依赖 Draw.io MCP，也不要求安装 Draw.io 应用。只有需要可视化编辑或导出时，才需要 Draw.io Desktop 或 diagrams.net。

**快速导航：** [为什么需要](#为什么需要这个-skill) · [完整示例](#查看完整示例) · [快速开始](#30-秒快速开始) · [安装](#安装) · [使用](#让-agent-生成或修改) · [增量修改](#编辑--检查--补丁) · [保守迁移](#保守元数据迁移) · [校验](#校验与输出可靠度) · [适用范围](#适用范围)

## 为什么需要这个 Skill

语言模型擅长理解参与方、流程顺序、判断条件和返回关系，但不擅长在直接生成 Draw.io XML 时同时规划所有坐标与连线折点。

| AI 直接生成 XML | `product-swimlane-drawio` |
|---|---|
| 语义与几何混在一份脆弱输出中 | 先用严格的语义模型表达流程 |
| 每次生成的布局与路由可能不同 | 确定性引擎可稳定重建同一输入 |
| 人工调整容易被后续生成覆盖 | 稳定 ID 与几何感知补丁保留兼容的本地调整 |
| 文件能打开就被当作完成证明 | 严格诊断与视觉检查分别报告 |

它面向产品工作中最常见的闭环：**AI 先完成 80%，人再在本地调整 20%，后续迭代继续保留已经完成的工作。**

## 可以获得什么

- **可编辑：** 原生未压缩 `.drawio`、全高度垂直泳道、本地拖拽编辑。
- **可靠：** 已确认主路径、确定性布局、正交路由、独立返回和重试通道、阶段带。
- **可维护：** 稳定语义 ID、`inspect`、`patch`、`compare`、默认保护几何、安全调整泳道和节点。保守迁移接口为符合条件的旧受管图提供显式元数据迁移路径。
- **可验证：** 严格 Schema、结构化诊断、路由与标签检查、带 SHA-256 的原子输出收据。

## 查看完整示例

![请求评审示例](examples/request-review/preview.png)

虚构且领域中性的[请求评审示例](examples/request-review/)采用 v3 `approval-loop` 模式，包含四条泳道、一个判断、一条紧凑的返工回路、长流程间距和阶段导航栏。目录中提供了[提示词](examples/request-review/prompt.md)、[语义规格](examples/request-review/process.json)和导出的[预览图](examples/request-review/preview.png)。

语义规格可以在本地确定性生成原生可编辑的 `.drawio` 文件，并通过零警告的严格校验。生成的 `.drawio` 不提交到仓库，GitHub 仅保留可直接引用和展示的 PNG 预览图。

## 30 秒快速开始

安装 Skill：

```bash
npx skills add zz-zed/product-swimlane-drawio
```

然后告诉 Agent：

```text
使用 product-swimlane-drawio 创建一张可编辑的垂直泳道图。
先确认泳道顺序、主路径、分支、返回关系和假设。
在我确认结构之前不要生成文件。
```

## 安装

所有安装方式都使用 `skills/product-swimlane-drawio` 下的同一份 Skill。运行时要求 Python 3.10+；Node.js 只在通过 `npx skills` 安装时需要。完整 Skill 目录才是运行单元：不能只复制 CLI 脚本，因为它依赖相邻的私有模块；不需要 pip 安装或配置 `PYTHONPATH`。

### 手动安装

#### Agent Skills

```bash
npx skills add zz-zed/product-swimlane-drawio
```

安装器会识别兼容的 Agent 并询问安装位置。添加 `-g` 可安装到用户级共享目录。仓库中只有一个 Skill，因此不需要 `--skill` 参数。

#### Claude Code Plugin Marketplace

在 Claude Code 中执行：

```text
/plugin marketplace add zz-zed/product-swimlane-drawio
/plugin install product-swimlane-drawio@product-swimlane-drawio
```

#### Codex Plugin Marketplace

```bash
codex plugin marketplace add zz-zed/product-swimlane-drawio
codex plugin add product-swimlane-drawio@product-swimlane-drawio
```

### 通过 Agent 安装

告诉 Codex、Claude Code 或其他兼容 Agent Skills 的编程 Agent：

> 请从 `github.com/zz-zed/product-swimlane-drawio` 安装 `product-swimlane-drawio`。优先使用当前 Agent 的原生 Plugin Marketplace；不支持时再使用 `npx skills`。

Agent 可能会询问安装范围，并在运行命令前请求授权。

### 验证安装结果

项目级安装使用 `npx skills list`，用户级安装使用 `npx skills list -g`。Marketplace 安装可通过 `claude plugin list` 或 `codex plugin list` 检查。

## 让 Agent 生成或修改

从零生成：

```text
使用 product-swimlane-drawio 将这个流程转换为可编辑的 Draw.io 泳道图。
先确认参与方、正常路径、判断、异常路径和完成状态。
我确认后再生成、严格校验并导出预览，视觉检查状态需要单独报告。
```

修改已有兼容图：

```text
使用 product-swimlane-drawio 修改这个 .drawio 文件。
以最近保存的文件为准，保留无关几何和人工 waypoint。
只应用我要求的语义变更，然后严格校验并对比结果。
```

## 工作原理

```text
自然语言流程
        ↓ 确认语义
版本化 JSON 模型
        ↓ 确定性生成
原生可编辑 .drawio
        ↓ 严格校验 + 预览
人工本地编辑
        ↓ 检查最新文件
保护几何的语义补丁
```

![从零生成与增量修改工作流](docs/illustrations/product-swimlane-readme/create-update.png)

引擎支持已确认的自上而下主路径、判断、跨泳道调用、返回、重试、同顺序交互和可选水平阶段。引擎会在有限预算内统一规划本次可变连线的端口，优先处理主路径，并在几何条件允许时将异常流量放到独立通道。

## 编辑 → 检查 → 补丁

本地编辑是设计的一部分，不是兜底手段。

1. 使用 Draw.io Desktop 或 diagrams.net 打开生成的 `.drawio`。
2. 调整文案、节点位置、泳道尺寸或连线并保存。
3. 将最近保存的文件重新交给 Agent。
4. Agent 运行 `inspect`，检查产物状态，将补丁绑定到返回的输入 SHA-256，准备最小语义补丁，保留无关几何，再校验并对比结果。

安全补丁依赖该 Skill 创建的语义元数据、匹配的语义模型哈希、稳定 ID 和经过检查的准确输入文件。补丁可以插入、调整宽度、重命名或安全删除泳道；v3 新增节点也可直接使用泳道内 slot 和说明节点 anchor，无需重建整张图。由此产生的后续泳道平移与自动重路由会单独回执。经确认的直接语义编辑可以显式建立新基线；结构异常或手工创建的 `.drawio` 可能需要迁移或受控重建。明确设置的人工 waypoint 不会被静默简化。

执行补丁及随后对比时应使用同一工具版本。0.6.0 复核由 0.5.1 已经完成的补丁结果时，可能仅因生产版本戳不同而报不通过，这不代表图已损坏。用新版修改受支持的旧输入、再用同一新版对比，是正常编辑流程。对比失败不能自动放行；只读复核不得改写版本戳或自动补丁、重建文件。详见[兼容矩阵](skills/product-swimlane-drawio/references/schema.md#compatibility)。

仅修改连线文案时，保存的端口和折点保持不变，包括仍标记为 automatic 的编辑器手工调整。标签优先保留原生位置，必要时只在原线路移动；节点或泳道变更使原线路失效时，需要明确声明对应边的 reroute。`reroute: true` 仍保留 explicit 折点，替换数组必须提供 `waypoints`。回执分别记录文案更新、标签移动、实际改线与依赖位移。

标签校验和避让读取当前原生几何；文字框为估算，不支持的样式明确报告不可测并使 strict 失败。compare 包含受管 cell 的扩展子树、混合文本、空白和顺序；实际序列化候选须通过检查后才原子写入。原生导出、代理看图与真人验收分别记录。

自动 build 和显式 reroute 会在最终校验前拒绝不可行或原生支持范围外的候选，非 strict 模式同样适用；空标签不再掩盖路径不支持。已有文件校验沿用命令和诊断严重级别约定，但保存的 v2/v3 跨泳道 automatic 回线不再要求目标泳道内的竖直走廊，因此警告及 strict 结果可能变化。同泳道回线检查及显式、已保存几何的保护保持。仅在 v3 中，同泳道向下的判断分支会在底部未被主路径保留时优先底出；显式端口仍优先。

## 保守元数据迁移

这套已集成的候选接口尚未正式发布。它仅覆盖这样一类旧图：**单页、受管、身份和同 schema 流程语义已经存在**，但元数据存在可由明确规则补齐的狭窄缺口。它不是导入器、schema 升级工具、身份采纳工具，也不会从布局或标签猜测语义。既有 `build`、`inspect`、`patch`、`validate` 和默认 `compare` 契约不变。

先做只读判定。dry-run 不写 XML，也不创建输出目录或候选文件：

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  migrate --input old.drawio --dry-run
```

回执包含输入 SHA-256、现有 managed state、分类、原因、同 schema 语义摘要、精确拟改字段、严格校验证据以及本次调用是否可写。五类迁移分类描述的是元数据可修复资格，而不是质量通过：

| 分类 | 含义 |
|---|---|
| `not-needed` | 没有可修复项；即使 producing stamp 旧或缺失，也不创建输出副本。 |
| `automatic` | 已有有效 hash，可唯一补齐缺失的派生 lane order 和/或 hash-rule version。 |
| `confirmation-required` | 历史 hash 缺失，但其它所需原始事实有效。用户必须在这一次调用中明确接受当前语义模型为新基线。 |
| `unsafe` | 已有 hash 漂移、不支持的 hash/schema rule、空或无效元数据，或核心事实缺失/矛盾，均不得修复。 |
| `unsupported` | 图形、原始 XML 载荷或所需语义采纳超出本轮保守范围。 |

接受标志不能覆盖 `unsafe` 或 `unsupported`，一次 dry-run 也不能授权后续写入。符合条件的修复必须使用新输出路径、已经审阅的 SHA，并且仅在“缺历史 hash”分类下使用这个窄接受标志：

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  migrate --input old.drawio --output migrated.drawio \
  --expected-input-sha256 "<dry-run或inspect返回的sha256>" \
  --accept-unverified-baseline
```

迁移没有 `--force`、原地覆盖、非 strict 模式、target-schema 转换或 model-drift 接受别名。候选会拒绝输入/输出别名和已存在目标，在原子、无覆盖交付前再次检查输入；只有 projected 与 serialized strict 校验、精确保护检查和独立重算的迁移比较都通过后才写入。`not-needed` 成功时仍为 `written: false`；分类不能把 strict 失败变成可交付结果。

最多只能变更已识别 pool 上的四个属性，并且每项都须满足各自条件：缺失的 `data-lane-order`、缺失的 `data-model-hash-version`、已经明确接受的缺失 `data-model-hash`，以及确有其它允许修复时的 `data-tool-version`。这是精确变更计划，不是允许任意 pool 差异的白名单；流程语义、几何、路由、未知 XML 载荷、text/tail 与同级顺序都受保护。

对已经交付的迁移执行独立核验。此模式与 patch changes 互斥，且绝不写入：

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  compare --before old.drawio --after migrated.drawio --migration
```

`compare --migration` 会从 before 重新计算计划，拒绝目标值错误、cell 错误、漏字段/多字段，以及任意语义、几何、XML 载荷、text 或顺序变化。迁移比较通过只证明该规则范围内的保护；它不证明用户接受了缺 hash 基线，也不能替代 strict 校验。

历史源码重建和可复现的中性合成变异可以证明格式规则，但不能证明真实历史缺字段原件兼容。历史编辑器保存、当前编辑器保存、导出、Agent 看图和真人验收是彼此独立的证据；尤其是真实历史缺字段原件仍需在取得后验证。

## 校验与输出可靠度

严格校验覆盖语义模型、主路径连续性、判断、重试、阶段、固定宽高比节点、文字适配、端口、泳道边界距离、节点穿越、短线段、过多折点、回钩、往返路径混淆、受支持样式的箭头末段净空、标签位置、连线重叠和阶段层级。对未覆盖的样式或形状，箭头检查会明确返回 `partial` 或 `not_available`；这表示证据不完整，不是推断通过。

![严格校验与视觉检查提供两类独立证据](docs/illustrations/product-swimlane-readme/quality-gate.png)

自动校验与视觉检查是两类不同证据：

| 检查能力 | 可以支持什么 | 必须说明 |
|---|---|---|
| 纯文本 Agent | 结构与路由可靠度来自严格校验 | 模型视觉检查报告为 `not_available` |
| 多模态 Agent | 额外检查文字裁切、视觉碰撞、箭头遮挡和过度绕行 | 分别报告严格校验、预览导出和视觉检查 |
| 多模态 Agent 加人工复核 | 重要图形公开发布或投入使用前的推荐方式 | 检查最终预览并保留可编辑源文件 |

本项目**不声明**模型生成流程图具有经过测量的准确率。预览导出成功不代表模型已经检查图片，多模态检查也仍可能漏检问题。

## 适用范围

| 支持 | 不作为目标 |
|---|---|
| 可编辑的产品和业务垂直泳道图 | 通用图形生成 |
| 以角色或系统划分泳道 | 严格 BPMN 合规 |
| 主路径、判断、分支、返回和重试 | UML、C4、ERD、网络或基础设施拓扑 |
| 新建流程和安全修改兼容流程图 | 自由排版的演示图形 |

## 架构与设计原则

[架构说明](docs/architecture.md)介绍组件与数据流；[设计原则](docs/design-principles.md)说明为什么需要将语义生成、确定性渲染、本地编辑和校验彼此分离。维护者可以继续阅读 [Process IR v3](docs/PROCESS_IR_V3.md)、[布局约定](docs/LAYOUT_CONTRACT_V3.md)、[往返编辑约定](docs/ROUND_TRIP_CONTRACT.md)和[基准计划](docs/BENCHMARK_PLAN.md)。

## 直接使用本地工具

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  build --spec process.json --output process.drawio --strict

python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  validate --input process.drawio --strict

python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  inspect --input process.drawio
```

补丁和对比：

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  patch --input process.drawio --expected-input-sha256 "<inspect返回的sha256>" --changes changes.json --output process-updated.drawio --strict

python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  compare --before process.drawio --after process-updated.drawio --changes changes.json
```

迁移命令与回执请见[保守迁移](#保守元数据迁移)和[语义 Schema 与补丁约定](skills/product-swimlane-drawio/references/schema.md#conservative-metadata-migration)。

详见[语义 Schema 与补丁约定](skills/product-swimlane-drawio/references/schema.md)。

## 安全与隐私

Skill 会使用调用 Agent 当前拥有的权限运行本地脚本。安装前请审阅 Skill 与脚本。公开 Skill 包不包含用户数据、组织名称、专有术语、生成后的流程图或特定领域示例流程。任务产物应放在 Skill 目录之外。

漏洞报告方式参见 [SECURITY.md](SECURITY.md)。

## 许可证

项目采用 [MIT License](LICENSE)。Draw.io 和 diagrams.net 是第三方产品，本项目与其维护方不存在隶属或官方认可关系。
