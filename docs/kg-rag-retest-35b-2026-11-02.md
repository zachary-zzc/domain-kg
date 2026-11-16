# 35B RAG/KG 新复测：裁判校准失败（2026-11-02）

**本次已重新调用 Alibaba API，但未获得新的 RAG/KG 质量对比结果。**
API 能力检查通过，裁判校准 12 个用例通过 11 个，正式检索阶段被既定门槛拦下。
失败记录保留，没有重试到通过、复用上轮通过的校准记录、修改分数或绕过门槛。

## 本次条件与实际执行

- 沿用冻结的 747 篇公开文档、23 道开发题：SQuAD 4 题、HotpotQA 8 题、MuSiQue 7 题、DROP 4 题。
- 计划比较 Vector RAG 与 KG 原文合并，两组统一使用 `qwen3.6-35b-a3b` Alibaba API。
- 复用经哈希验证的图与 embedding 排名；本次没有重建图或重新计算整库 embedding。
- 沿用 `kg-quality-v7-independent-metrics`：相同来源排序器、reader、语义裁判、独立复核和预算。
- 新目录没有复制旧回答、排序响应或裁判响应。代码、语料、问题、协议与图封存核验通过。
- API 能力检查完成 4 次请求，reader、构图 smoke 和 embedding 均通过。
- 校准完成 40 次请求，无 API 错误、无未确认请求。12 个用例中 11 个通过，1 个存在语义判断分歧。
- 正式排序、23 题回答、检索评分和问答评分均未开始；两组各计划 23 题，实际评估 0 题。

## 校准失败原因

用例 `partial-bridge` 的问题是 “Where was the author of Blue born?”，唯一来源是
“Blue was written by Ada.”。该来源提供必要的作者身份桥接，但缺少出生地，按固定定义应为
`useful` 来源和 `partial` 证据。

| 判断 | 来源有用性 | 最终所问信息 | 程序导出的充分性 |
|---|---|---|---|
| 本次主评 | irrelevant | missing | insufficient |
| 本次独立复核 | useful | missing | partial |
| 上轮相同主评请求 | useful | missing | partial |
| 上轮相同复核请求 | useful | missing | partial |

已核验本次与上轮两种请求的完整 payload 哈希分别一致；温度均为 0，模型 ID、提示、来源和
解析代码未变。请求相同仍获得不同的语义标签，说明至少这一校准用例上的评审输出不稳定；
现有记录不足以判断服务端具体原因。

这不是答案 exact match，也不是格式或连接失败。程序比较的是预先规定的语义类别；
主评没有遵守提示中“必要桥接也属于有用证据”的定义。复核捕获了 source precision 的分歧。
两种充分性标签在二值主指标中都为 0，但它们对来源有用性的判断不同，校准不能因此放行。
该问题直接影响对图召回的价值判断：只提供中间桥接的原文可能被误判为无关。

## 本次八项指标

下表的“未运行”不是 0 分，也不是已经运行后裁判无法确定。正式问题两组各为 0/23 已评估，
不存在可以计算的成功率、均值或配对差。

| 固定指标 | Vector RAG | KG + 原文合并 |
|---|---|---|
| Evidence sufficiency@12 | 未运行 | 未运行 |
| Semantic information coverage@12 | 未运行 | 未运行 |
| Semantic source precision@12 | 未运行 | 未运行 |
| Semantic correctness | 未运行 | 未运行 |
| Grounded correctness | 未运行 | 未运行 |
| Answer completeness | 未运行 | 未运行 |
| Citation support | 未运行 | 未运行 |
| Unsupported-claim rate ↓ | 未运行 | 未运行 |

因此本次不能证明 KG 优于或劣于 RAG，也不能声称算法发生质量回退。
上轮暂定八项指标保留在 [上轮报告](kg-source-merge-35b-2026-11-02.md)，不是本次新结果。
上轮可比较题目的主要指标配对持平，且未知评分较多，尚未证明 KG 增益。

## 验证与记录

本次没有修改算法、评估提示、解析器或指标定义。相关单元验证 **53 passed**：

```bash
.venv/bin/python -m pytest -p no:capture -q \
  tests/unit/test_kg_quality.py \
  tests/unit/test_kg_quality_run.py \
  tests/unit/test_source_rerank.py
```

原始记录位于 `.cache/kg-rag-retest-35b-2026-11-02/`：

- `evaluation/assessments/` 与 `evaluation/calls/`：40 次新校准请求和响应。
- `evaluation/calibration-summary.json`：12 个校准用例的结果。
- `calibration-audit.json`：封存核验、请求完整性、同请求前后标签对照及未运行阶段。
- `preflight.log`、`calibration.log`、`retrieve.log`：实际执行与拦截原因。
- `code/`：本次代码快照。

本次能力检查与校准共记录 44 次 API 请求、29,137 tokens；未核实价格，不估算费用。
没有正式检索或回答延迟数据。图结构沿用上轮，不把缓存图统计当作新构图结果。

后续正式比较仍需通过裁判校准。若修订通用裁判提示或判断方式，需要另立协议版本，
同时应用于 RAG/KG 并重新校准；不能仅针对这道用例改分或用重试选出通过结果。
