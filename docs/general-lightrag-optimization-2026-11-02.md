# 通用提取、LightRAG 优化与复测（2026-11-02）

通用提取和切分改造已实现，四档 Alibaba API 实验已结束。构建覆盖率明显高于先前
35B 构建诊断，但**本轮没有可靠证据证明 KG 提高了检索或回答质量**。已核实的问题包括
融合排序丢失桥接来源、呈现的路径只有一跳、模型漏报关系端点，以及裁判标签与其解释
矛盾。原始评分保留，不据此宣布算法质量提升。

公开主实验是 747 个文档单元、754 个窗口、23 道开发题；荷尔蒙资料是另一个四窗口
诊断。两者不混合计算。

## 实现与上游方法


The prior pipeline already called a remote LLM for extraction, then used LightRAG
for scoped mapping. It lacked a normal second-pass coverage review; its semantic
regex veto was removed in the preceding audit. This revision follows the pinned
LightRAG 1.5.7 initial-extraction/one-gleaning pattern through the existing authorized
provider adapter. It does not replace the evidence ledger with native unscoped
LightRAG writes.

`lightrag_general_v1` is the default for newly configured profiles, and explicit in
the new experiment. The prompt uses general entity/relationship extraction, binary
decomposition, source-derived labels, directions and scope. It has no domain-specific
role dictionary, required facet list, minimum edge count or predefined type vocabulary.
Gleaning reads the original source and a compact inventory, then returns additional
facts with independently validated endpoints/references. Correction and gleaning share
one additional call. Initial transport failures are not retried; every call retains
authorization, source scope, token/response limits and usage accounting.

Qualifier names are now bounded content-derived strings, with source-backed values.
Optional absent qualifier arrays have the same empty meaning as the canonical model.
Other malformed model output is corrected by the model once, never by regex or field
coercion. Existing typed/source/authority validation remains mandatory. Two valid
responses may be combined without forcing their union into one response's record
limit. Isolated automatic mentions are omitted; searchable source text is preserved.

Three source-subdivision paths share `semantic-text-splitter==0.33.1` with exact
character offsets and token callbacks. The previous Chinese delimiter list is gone.
Source coverage, bounds, progress and row provenance remain tested. The old
chemical-list test no longer asserts that a Unicode splitter recognizes complete
biochemical names: that was a domain-specific delimiter guarantee, not a general
semantic guarantee. The input remains a coverage/offset regression fixture. Library
fallback can still split an entity longer than the available structural unit;
downstream semantic completeness requires model evaluation.

This is a maintained adaptation of the algorithm, not identical native LightRAG:
we retain directed predicates, open labels, exact evidence, permissions, independent
support ownership and staged publication. Upstream's undirected/name-based merging,
longer-description preference and suggested entity taxonomy are not used to overwrite
these contracts. Accepted graphs, prior experiments and scores remain unchanged.

References inspected: [LightRAG extraction/gleaning](https://github.com/HKUDS/LightRAG/blob/main/lightrag/operate.py),
[LightRAG prompts](https://github.com/HKUDS/LightRAG/blob/main/lightrag/prompt.py), and
[text-splitter](https://github.com/benbrandt/text-splitter). The implementation audit
used installed LightRAG 1.5.7; no upstream code was vendored or local model run.

## 修复的另一个通用故障

真实响应暴露了格式纠正路径中的丢失：第一次输出格式不合格，第二次格式正确但某条
关系引用错误时，整个窗口仍被拒绝。现已让最终纠正响应使用既有的独立事实校验，
保留验证通过的子集并记录拒绝数。没有修改原文、补造端点或放宽证据校验。

新增回归先失败，应用 `partial=True` 后通过。为避免重抽样影响比较，在独立代码快照
中重放**完全相同的已记录请求和响应**，没有重新调用构建模型：

| 工作模型 | 修复前失败窗口 | 相同响应重放后 | 窗口总数 |
| --- | ---: | ---: | ---: |
| 27B | 64 | 10 | 754 |
| 35B-A3B | 11 | 3 | 754 |
| 122B-A10B | 9 | 8 | 754 |

最终四模型结果在 `evaluation-repaired/`，`evaluation-final/` 保留原始构建记录。
旧 worker 在四图封存后停止，没有新的旧版 QA 请求；随后将相同修复应用到工作区。
生产与评测文件内容哈希与修复快照一致。冻结校验在原快照目录执行通过；校准文件的
绝对路径属于快照身份，工作区同内容文件不是同一个路径。

## 验证与 smoke test

- 最终 **206 项**相关单元/评测测试通过；首次实现前的九项回归已观察到失败。
- 此前 **23 项不同的真实存储集成用例**通过，覆盖实体解析、检查点、窗口、provider
  预算及开放图发布；最终这一行恢复逻辑修改后未重复全部存储集成测试。
- Ruff、11 个生产模块的 strict mypy、依赖兼容性检查、`git diff --check` 通过。
- 公开来源的 35B 实时 smoke 完成两次 API 调用，产生 5 个节点观察、4 条关系观察。
  四个工作模型预检、固定裁判的 12 项语义校准通过；后者不保证真实题裁判正确。
- 全部模型推理走远端 API，本机没有加载或运行模型。已接受知识库没有迁移。

荷尔蒙 smoke 使用先前已选择的四个解析窗口，35B 构建、397B 语义核查接受的关系。
第一次有两个 `ConnectError`；只对这两个窗口做了一次单独记录的诊断重试，原始失败
不覆盖，正式公开实验也没有因此补跑失败评分。

| 窗口 | 第一次 | 单独重试 | 接受节点/关系 | 诊断 |
| --- | --- | --- | ---: | --- |
| h-11，价格/采样 | 完成 | 无 | 12 / 9 | 9 条均获文本裁判支持标签，另拒绝 2 条 |
| h-26，生理作用/限制 | 连接失败 | 完成 | 12 / 12 | 6 组缩写事实使用两个同义谓词重复；生理作用目标实体仍漏报 |
| h-100，方法/样本/运输 | 连接失败 | 完成 | 4 / 2 | 漏报关系目标 entity 等导致 11 条被拒绝；接受的 2 条获支持标签 |
| h-124，碎片化 OCR 表格 | 完成但空图 | 无 | 0 / 0 | 14 条被拒绝；不能把空图解释成修复了表格理解 |

h-26 的 12 条亦均获支持标签，但不代表 12 条不同知识。流程保持了校验，**未通过完整
知识覆盖的质量验收**。该诊断无穷尽的人工参考事实，因此无可报告的召回率；文本裁判
也不能验证原 PDF 行列对应。以上问题未使用新增词表或正则修补，原文仍供文本检索。

## 固定主实验

公开池含 747 个短文档单元，标题、换行和正文共 **405,461 UTF-8 bytes，约 396 KiB**：
MuSiQue 458、HotpotQA 242、SQuAD 24、DROP 23。固定审查记录纳入 HotpotQA 8、
MuSiQue 7、SQuAD 4、DROP 4，共 23 题。这是开发试跑集，不是未见留出集或全量
公开 benchmark。荷尔蒙资料不在这个主实验中。

每个模型独立建图并用自身回答。统一 embedding 为 `text-embedding-v4`/1024 维，
统一裁判为 `qwen3.5-397b-a17b`。两组仍是纯向量 `vector_rag` 和
向量+BM25+图的 `domain_kg`。检索没有生成式查询模型。来源最多 12 个，原文与路径
合计最多 14,000 Unicode 字符。问题、评分标准、裁判、上下文和输出预算均未改动。

主实验保留原来的 754 个初始窗口，避免更换检索语料造成混杂；输出溢出时使用新
拆分器，初始切分与请求重新打包另有回归测试。因此本轮不是所有切分策略的纯净
前后对比。dense 排名仅在语料、问题哈希、embedding 和维度完全相同时复用；不跨
模型借图。相同评分请求按完整哈希去重，并非独立重复样本。

检索按语义信息覆盖、证据充分性、来源有用性评分；回答按语义正确、原文支持、
完整性、引用支持、无依据声明评分。没有答案 exact match 或逐字覆盖要求。来源 ID
和引用范围的精确校验只保证评分引用实际提供的来源，不作为语义质量分数。

## 原始语义分数及有效性限制

以下是**未改写的模型初评**。百分比以可评分题数为分母，括号为可评分题数/23。
缺失项保留在计划分母并报告上下界；不能只比较不同分母的百分比。

| 模型 | Vector 检索充分@12 | KG 检索充分@12 | Vector 有依据正确回答 | KG 有依据正确回答 |
| --- | ---: | ---: | ---: | ---: |
| 27B | 95.5%（22/23） | 86.4%（22/23） | 90.5%（21/23） | 86.4%（22/23） |
| 35B-A3B | 95.5%（22/23） | 86.4%（22/23） | 77.3%（22/23） | 82.6%（23/23） |
| 122B-A10B | 95.5%（22/23） | 86.4%（22/23） | 85.7%（21/23） | 86.4%（22/23） |
| 397B-A17B | 95.5%（22/23） | 86.4%（22/23） | 90.5%（21/23） | 95.0%（20/23） |

检索初评在四图上相同，不是 92 个独立问题。@12 两组各自全计划充分率区间为
91.3–95.7% 和 82.6–87.0%；逐题双方均有结果的 21 对中 KG 0 胜、1 负、20 平，
另 2 对未知。@3 已知充分率为 17/23 对 19/23，不能把较小 k 的优势归因于 KG，
因为同时引入 BM25。完整 @1/3/5/10/12 及其余指标见最终 `summary.json`。

回答主指标的原始配对比较如下，亦受后述裁判错误影响，不能作为优化验收依据。

| 模型 | 双方可评分对数 | KG 胜/负/平 | 全计划差值范围（百分点） |
| --- | ---: | ---: | ---: |
| 27B | 20/23 | 1 / 2 / 17 | −8.7 至 +4.3 |
| 35B-A3B | 22/23 | 2 / 1 / 19 | +4.3 至 +8.7 |
| 122B-A10B | 20/23 | 1 / 1 / 18 | −4.3 至 +8.7 |
| 397B-A17B | 18/23 | 1 / 1 / 16 | −8.7 至 +13.0 |

这是缺失项上下界，不是置信区间。184 个计划回答评分有 12 个未知；@12 证据评分有
8 个未知，含跨模型复用的相同失败。运行状态为执行结束但 `semantic_ready=false`。
连接失败、中断后未知结果、裁判协议错误均保留；未知项没有计零。

**逐题核查发现明确裁判错误**：Bob Saget 与 S. Shankar 谁更年长这道题，两组四模型
全部回答 Bob Saget。引用原文分别给出 1956 与 1963 年，支持该答案。但 vector 组
裁判 JSON 标为 `incorrect`/`missing`，解释又明确承认 Bob Saget 正确。这造成每档
一个虚假的 KG 胜例。`judgment-audit.json` 保存原文和审计，未回写原始分数。
因此 35B 的表面正增益也不能据此成立；校准全部通过不等于裁判已足够可靠。

另有裁判把错误答案的 requirement 标为 covered，完整性子指标也存在不一致。
DROP 的“问数量却返回年份列表”等边界例需要复核，而非加逐字匹配补丁。保留初评
及审计标记，不选择对 KG 有利的标签，也不补跑到分数满意为止。

## 图结构：开放类型不等于质量

| 模型 | 节点观察 | 关系观察 | 字面节点类型 | 字面谓词类型 | 有关系文档 | 失败窗口 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 27B | 5,234 | 5,088 | 920 | 2,354 | 711/747 | 10/754 |
| 35B-A3B | 7,104 | 7,250 | 1,211 | 3,018 | 731/747 | 3/754 |
| 122B-A10B | 5,632 | 5,877 | 1,140 | 2,720 | 722/747 | 8/754 |
| 397B-A17B | 7,366 | 7,632 | 1,657 | 3,233 | 731/747 | 4/754 |

35B 先前 roles/facets-v2 诊断的有关系文档分别为 591/747、662/747，失败窗口为
129/754、75/754。本次说明更多来源完成构建，但跨版本变化同时含格式、限定词、
校验恢复和 prompt 改动，不能单独归功于 gleaning。

四图自动孤立节点均为 0，这是删除未连接自动提及的设计结果，不是知识完整性。
仅出现一次的谓词标签约占 64–70%，包含同义词与大小写变体。字符串去重没有消除
全部同义事实，不能把字面类型数当作语义多样性。

| 模型 | 检索名称连接后节点 | 弱连通分量 | 最大分量占比 | 跨文档出现节点 |
| --- | ---: | ---: | ---: | ---: |
| 27B | 4,245 | 500 | 17.3% | 327 |
| 35B-A3B | 5,592 | 475 | 29.0% | 434 |
| 122B-A10B | 4,332 | 460 | 15.4% | 366 |
| 397B-A17B | 5,757 | 483 | 30.1% | 469 |

这些名称连接是未验证的检索提示，不是生产中已接受的实体合并。全图没有独立语义
precision/recall 金标准，结构校验只证明来源/端点合法。四模型分别有 422、465、
543、444 个窗口带关系拒绝警告，“失败窗口少”不等于“没有事实遗漏”。任一窗口
彻底失败仍排除整个文档的图，双方文本检索池保留完整语料。

## 低收益的已核实机制

1. **融合丢掉已经召回的桥接来源。** Museum 区域题的 South Central Coast 来源
   在 vector 排第 3，融合后第 27，被 top-12 排除。四图均已抽出它与 Đà Nẵng 的关系。
   梵蒂冈建国日期的来源也从 vector 第 11 降至融合第 26–27；122B 图分支甚至排它
   第 5，融合仍未让它入选。这不是单纯提取不出来。
2. **图很少改变来源选择。** 对相同冻结数据离线去掉图分支后，27B/35B/397B 各有
   22/23 个来源列表不变，122B 为 23/23。图发现了新候选，但每档最终只选入 0–1 个
   纯图来源。`fuse_evidence` 为已有 vector/lexical 来源保留分数，只添加图路径；
   双路文本命中通常压过单路桥接。该消融未增加正式评分组或模型调用。
3. **多跳路径没有呈现。** 各图有 20/21/20/21 题带路径，展示的 45/42/37/42 条路径
   全为一跳。11/16/11/18 题遍历触及截断。两跳配置不等于有效多跳推理，广泛种子、
   最短路径选择、名称变体和路径预算还需分别核实。
4. **结构缺失及同义重复。** h-26/h-100 中模型写出属性关系，却漏报目标实体，
   纠正后仍遗漏；验证器必须拒绝。h-26 还重复缩写事实。不能由本地代码猜类型、
   补实体或连接相邻表格单元来增加边数。
5. **回答错误和裁判错误要区分。** 即使原文充分仍有事件先后、计数、主体混淆；
   缺桥接时会把 A Lưới 区域套给 Da Nang，或把 Jan Bull 出生年套给 Will Self。
   另有上述裁判假负例，更多边不能自动解决。

下一步应对称复核裁判的语义标签一致性，再用相同标准单独试验检索融合与真正的多跳
桥接；提取方面改善模型端点完整性、同义事实一致性。不能继续加领域词表或针对某题
的规则。本次未改冻结检索策略来掩盖负结果。旧检索层仍有词面分词和谓词提示表；
本次移除的是提取语义和切分中的专用规则，不能声称整个应用没有 regex 或固定提示。

## 性能、调用和复现边界

离线检索 p50：vector 约 1 ms；KG 四档约 40/54/53/61 ms。仅测缓存排名和内存适配器，
不含远端 query embedding、数据库和服务端网络延迟。没有端到端提速结论。

原始构建完成响应记录 6,011 次，报告约 12.45M tokens；回答与裁判合并完成记录
539 次，约 1.44M tokens。重放没有新增构建推理，复制记录不能重复计费。没有核实
价格表，费用字段未知。相同请求按哈希复用，不是独立重复实验。

构建每模型并发从 4 提升到 16 时，16 个先前请求中断，结果/计费未知；记录保留，
重启可能重复这些只读推理。重放首次遇到源响应缺失后停止，另中断 4 个语义请求，
这些评分保持未知、没有重发。重复 catalog GET 的 TLS 失败未产生推理，使用同配置
已通过预检并保留来源。重启、缓存、并发变化不允许作干净的整轮耗时比较。

产物均在 Git 忽略的 `.cache/kg-general-2026-11-02/`：

- `evaluation-repaired/{report.md,summary.json,scored-rows.json,packets.json}`：正式原始评分。
- `evaluation-repaired/construction/*/graph-statistics.json`：结构、失败、重复统计。
- `repair-code/`：实际评测代码快照；`evaluation-final/`：原始远端构建记录。
- `retrieval-diagnostic.json`、`bridge-diagnostic.json`：离线消融及桥接排名。
- `judgment-audit.json`：带原文及输入哈希的裁判冲突审计，不替换正式评分。
- `hormone-smoke/`、`hormone-smoke-transport-followup/`：原始 smoke、失败、独立重试。

问题、参考答案、裁判协议和以往冻结结果没有改写。新浏览器配置及 Alibaba 示例使用
`lightrag_general_v1`；已存 profile 保持原策略，生产知识库切换不在此次实验内。
