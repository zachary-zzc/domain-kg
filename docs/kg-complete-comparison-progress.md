# KG 构图质量、模型规模与检索实验（阶段报告）

2026-10-23。目标是让 RAG 辅助构建有原文依据的准确知识图谱，再检验图谱能否提供更好的检索证据。所有当前模型推理均通过 API；本地模型已停止。

[完整表格与逐项记录](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-comparison-progress.xlsx) · [离线节点图下载](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs-api.zip) · [节点图目录](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/index.html)

状态：8/10 个配置（8 个模型、另 2 个 Schema 适配配置）完成 64 段三方式构图及主裁判审计；10/10 组全量检索完成。未完成组保留记录，不用缓存子集排名。数据库回归状态见下文。

已完成的四个较大模型都从原文校正获得事实支持率提升，但额外邻文和图提示没有稳定带来进一步提升。完整的 1,000 题比较没有证明校正图＋RAG 稳定优于强 RAG，也没有随参数量增大而单调提高的结果。构图更准确和检索更有效需要分别验收。

## 我们的方法多了什么

| 方式 | 构建输入与处理 | 回答阶段的证据 |
| --- | --- | --- |
| 强 RAG | 保留完整原文；共用 embedding 与 BM25 索引，无生成式构图 | 向量与词法召回后 RRF 装入原文 |
| 普通 KG 对照 | 模型直接从原文抽取带引用的关系 | 35B 独立图检索对照以实体词法种子探索图，不使用向量／文本候选 |
| KG＋原文校正 | 先直接抽取，再由同一模型只依据主原文纠正关系、角色、方向与限定条件 | 受控构图实验，用于分离原文校正的贡献 |
| KG＋RAG＋原文校正 | 校正时增加相关原文和同模型已有图提示；事实仍须由主来源支持 | 共用强 RAG，加关系约束的图召回、融合及带来源的关系路径 |

普通 KG 在此特指本项目的直接抽取对照，不代表所有 KG 产品。正式服务的修改包括：图排序从 UUID 枚举改为可见来源的相关性与支持路径排序；图重复命中不再无条件获得第三张 RRF 票；图路径与原文一起遵守证据预算。离线适配器复用正式排序、遍历、融合与装入函数，但数据库身份与发布约束由真实存储测试另行验证。

关系附带原文定位只是可追溯性，不自动证明关系正确。例如，把死因写到 died_on、把乐队写到 occurred_on、反转 created_by，虽然可能引用了真实句子，依然不能作为可靠知识边。别名、近义表达、代词消解和来源内合理推断可以通过，不要求字符串或三元组 exact match。

## 相同来源与预算下的构图比较

从固定公共语料中按哈希预选 64 段，每个数据集 16 段；每段完整传入，三种方式均最多 8 条事实、1,536 输出 token。各模型自己的直接抽取结果供自己的校正使用，不借用更大模型的图。构建输入不含问题、答案、参考清单或裁判结论。

| 模型 | 方式 | 非空来源 | 保留事实 | 事实支持率 | 确认参考覆盖 | 未判事实 |
| --- | --- | --- | --- | --- | --- | --- |
| Llama 3.2 1B | 直接 KG | 0/64 | 0 | N/A | 0/385 (0.00%) | 0 |
| Llama 3.2 1B | KG＋原文校正 | 0/64 | 0 | N/A | 0/385 (0.00%) | 0 |
| Llama 3.2 1B | KG＋RAG＋原文校正 | 0/64 | 0 | N/A | 0/385 (0.00%) | 0 |
| DeepSeek R1 Distill Qwen 1.5B | 直接 KG | 0/64 | 0 | N/A | 0/385 (0.00%) | 0 |
| DeepSeek R1 Distill Qwen 1.5B | KG＋原文校正 | 0/64 | 0 | N/A | 0/385 (0.00%) | 0 |
| DeepSeek R1 Distill Qwen 1.5B | KG＋RAG＋原文校正 | 0/64 | 0 | N/A | 0/385 (0.00%) | 0 |
| Llama 3.2 3B | 直接 KG | 11/64 | 19 | 26.32% | 3/385 (0.78%) | 0 |
| Llama 3.2 3B | KG＋原文校正 | 9/64 | 10 | 30.00% | 1/385 (0.26%) | 0 |
| Llama 3.2 3B | KG＋RAG＋原文校正 | 7/64 | 11 | 18.18% | 1/385 (0.26%) | 0 |
| Llama 3.1 8B FP8 | 直接 KG | 19/64 | 112 | 50.00% | 39/385 (10.13%) | 0 |
| Llama 3.1 8B FP8 | KG＋原文校正 | 37/64 | 193 | 59.07% | 82/385 (21.30%) | 0 |
| Llama 3.1 8B FP8 | KG＋RAG＋原文校正 | 35/64 | 183 | 56.83% | 76/385 (19.74%) | 0 |
| Llama 3B＋Schema（补充） | 直接 KG | 54/64 构建记录 | 待完成 | 待完成 | 待完成 | 待完成 |
| Llama 3B＋Schema（补充） | KG＋原文校正 | 0/64 构建记录 | 待完成 | 待完成 | 待完成 | 待完成 |
| Llama 3B＋Schema（补充） | KG＋RAG＋原文校正 | 0/64 构建记录 | 待完成 | 待完成 | 待完成 | 待完成 |
| Llama 8B FP8＋Schema（补充） | 直接 KG | 53/64 构建记录 | 待完成 | 待完成 | 待完成 | 待完成 |
| Llama 8B FP8＋Schema（补充） | KG＋原文校正 | 0/64 构建记录 | 待完成 | 待完成 | 待完成 | 待完成 |
| Llama 8B FP8＋Schema（补充） | KG＋RAG＋原文校正 | 0/64 构建记录 | 待完成 | 待完成 | 待完成 | 待完成 |
| Qwen 27B Dense | 直接 KG | 62/64 | 290 | 76.55% | 172/385 (44.68%) | 0 |
| Qwen 27B Dense | KG＋原文校正 | 60/64 | 269 | 84.01% | 170/385 (44.16%) | 0 |
| Qwen 27B Dense | KG＋RAG＋原文校正 | 58/64 | 257 | 82.88% | 163/385 (42.34%) | 0 |
| Qwen 35B / A3B | 直接 KG | 60/64 | 273 | 69.06% | 136/385 (35.32%) | 8 |
| Qwen 35B / A3B | KG＋原文校正 | 63/64 | 248 | 78.42% | 140/385 (36.36%) | 7 |
| Qwen 35B / A3B | KG＋RAG＋原文校正 | 62/64 | 243 | 78.90% | 142/385 (36.88%) | 6 |
| Qwen 122B / A10B | 直接 KG | 62/64 | 394 | 76.27% | 196/385 (50.91%) | 19 |
| Qwen 122B / A10B | KG＋原文校正 | 64/64 | 428 | 83.86% | 228/385 (59.22%) | 19 |
| Qwen 122B / A10B | KG＋RAG＋原文校正 | 64/64 | 435 | 81.49% | 224/385 (58.18%) | 19 |
| Qwen 397B / A17B | 直接 KG | 64/64 | 385 | 74.29% | 208/385 (54.03%) | 0 |
| Qwen 397B / A17B | KG＋原文校正 | 63/64 | 380 | 81.32% | 221/385 (57.40%) | 0 |
| Qwen 397B / A17B | KG＋RAG＋原文校正 | 63/64 | 381 | 80.05% | 219/385 (56.88%) | 0 |

事实支持率＝来源支持的关系／完成语义判定的关系；空图为 N/A，不能算作 100% 正确。覆盖保留空图和构图失败；64 段中只有 55 段参考清单有效，经独立复核共 385 条参考命题。参考清单非穷尽，正确事实无需匹配清单也可以判定有据。逐事实表记录原文、理由、极性、限定条件和参考映射。

参考校验的原响应填入了未使用的 matches 字段，导致旧解析器将 55 份响应拒绝。本轮保存原始失败记录和响应，仅在独立修复层清空该无关字段，重新验证完整 ID、类型和 verdict；未改动支持判断、理由或候选事实匹配，也没有新增 API 调用。另 9 段无有效清单仍记缺失。该事后解析修复及哈希记录见 inventory-recovery-amendment.json。

### 校正相对直接抽取的变化

| 模型 | 比较 | 支持率差值 | 95% 区间 | 覆盖率差值 | 95% 区间 |
| --- | --- | --- | --- | --- | --- |
| DeepSeek R1 Distill Qwen 1.5B | KG＋原文校正 − 直接 KG | N/A | N/A | +0.00 pp | [+0.00, +0.00] pp |
| DeepSeek R1 Distill Qwen 1.5B | KG＋RAG＋原文校正 − KG＋原文校正 | N/A | N/A | +0.00 pp | [+0.00, +0.00] pp |
| Qwen 27B Dense | KG＋原文校正 − 直接 KG | +7.46 pp | [+2.86, +12.15] pp | -0.52 pp | [-4.32, +2.44] pp |
| Qwen 27B Dense | KG＋RAG＋原文校正 − KG＋原文校正 | -1.14 pp | [-3.28, +0.82] pp | -1.82 pp | [-6.84, +2.73] pp |
| Qwen 35B / A3B | KG＋原文校正 − 直接 KG | +9.37 pp | [+4.21, +14.32] pp | +1.04 pp | [-2.61, +4.53] pp |
| Qwen 35B / A3B | KG＋RAG＋原文校正 − KG＋原文校正 | +0.48 pp | [-2.41, +3.95] pp | +0.52 pp | [-2.21, +3.59] pp |
| Qwen 122B / A10B | KG＋原文校正 − 直接 KG | +7.60 pp | [+4.01, +11.31] pp | +8.31 pp | [+4.37, +12.93] pp |
| Qwen 122B / A10B | KG＋RAG＋原文校正 − KG＋原文校正 | -2.37 pp | [-4.72, -0.17] pp | -1.04 pp | [-3.50, +1.32] pp |
| Qwen 397B / A17B | KG＋原文校正 − 直接 KG | +7.03 pp | [+2.78, +11.57] pp | +3.38 pp | [-0.52, +7.61] pp |
| Qwen 397B / A17B | KG＋RAG＋原文校正 − KG＋原文校正 | -1.26 pp | [-3.17, +0.43] pp | -0.52 pp | [-3.08, +1.86] pp |
| Llama 3.2 1B | KG＋原文校正 − 直接 KG | N/A | N/A | +0.00 pp | [+0.00, +0.00] pp |
| Llama 3.2 1B | KG＋RAG＋原文校正 − KG＋原文校正 | N/A | N/A | +0.00 pp | [+0.00, +0.00] pp |
| Llama 3.2 3B | KG＋原文校正 − 直接 KG | +3.68 pp | [-25.00, +26.67] pp | -0.52 pp | [-1.34, +0.00] pp |
| Llama 3.2 3B | KG＋RAG＋原文校正 − KG＋原文校正 | -11.82 pp | [-33.33, +20.60] pp | +0.00 pp | [+0.00, +0.00] pp |
| Llama 3.1 8B FP8 | KG＋原文校正 − 直接 KG | +9.07 pp | [-3.70, +22.19] pp | +11.17 pp | [+6.67, +16.20] pp |
| Llama 3.1 8B FP8 | KG＋RAG＋原文校正 − KG＋原文校正 | -2.24 pp | [-10.80, +6.86] pp | -1.56 pp | [-6.57, +3.44] pp |

按来源配对 bootstrap 5,000 次，固定种子 20261021；未做多重比较校正。自动裁判也有本体表达偏好：例如把“第二张个人专辑”放在 is_a 的客体，可能因类型表达粗糙被判 uninformative，尽管其自然语言含义有来源支持。此类判定应结合逐项理由理解，不能把自动支持率当成人工认证精度。固定四段的第二裁判和原文人工式检查用于显示分歧，不覆盖主裁判分数。

| 模型 | 双裁判重合事实 | 完全同判 | 支持／非支持同判 |
| --- | --- | --- | --- |
| DeepSeek R1 Distill Qwen 1.5B | 0 | 0 | 0 |
| Qwen 27B Dense | 29 | 25 | 26 |
| Qwen 35B / A3B | 46 | 41 | 46 |
| Qwen 122B / A10B | 45 | 35 | 41 |
| Qwen 397B / A17B | 49 | 35 | 44 |
| Llama 3.2 1B | 0 | 0 | 0 |
| Llama 3.2 3B | 2 | 1 | 2 |
| Llama 3.1 8B FP8 | 16 | 14 | 16 |


## 全量检索对照

每组使用 1,000 题及全部关联的 6,566 段原文；HotpotQA、MuSiQue、DROP、SQuAD 各 250 题。825 题原标可答、175 题原标无答案。这里的全量是本轮固定实验集，不是上游数据集全部题目。小模型的 64 段构图诊断不能冒充其全量检索结果。

强 RAG 使用共用 text-embedding-v4 精确余弦前 50、BM25 前 50 与 RRF；回答读者固定为 35B，主语义裁判固定为 2.4T。各组最多 12 段、14,000 字符，图路径也计入预算。图组构建模型变化，回答模型不变化。原始旧参考不是唯一真值，允许原文支持的等价答案、直接推断和合理解释；旧参考缺陷、歧义和未判单独记录。

| 检索配置 | 完成记录 | 证据有用 | 证据可答 | 语义正确 | 有据正确 | 任一未判 |
| --- | --- | --- | --- | --- | --- | --- |
| 强 RAG | 1000/1000 | 96.00% | 77.58% | 65.41% | 63.15% | 3 |
| 校正 KG＋RAG · Qwen 27B Dense | 1000/1000 | 96.61% | 78.55% | 64.56% | 63.27% | 3 |
| 校正 KG＋RAG · Qwen 35B / A3B | 1000/1000 | 96.00% | 78.42% | 63.30% | 61.33% | 5 |
| 校正 KG＋RAG · Qwen 122B / A10B | 1000/1000 | 95.88% | 78.67% | 63.83% | 62.06% | 4 |
| 校正 KG＋RAG · Qwen 397B / A17B | 1000/1000 | 96.85% | 78.42% | 64.36% | 62.55% | 2 |
| 直接 KG＋RAG · Qwen 27B Dense | 1000/1000 | 96.36% | 76.85% | 63.06% | 61.58% | 4 |
| 直接 KG＋RAG · Qwen 35B / A3B | 1000/1000 | 96.24% | 78.06% | 65.13% | 62.79% | 2 |
| 直接 KG＋RAG · Qwen 122B / A10B | 1000/1000 | 95.15% | 77.58% | 63.59% | 62.06% | 3 |
| 直接 KG＋RAG · Qwen 397B / A17B | 1000/1000 | 94.30% | 76.61% | 64.96% | 62.06% | 21 |
| 直接 KG 独立检索 · 35B | 1000/1000 | 64.85% | 45.70% | 40.95% | 37.33% | 9 |

证据有用、可答、有据正确以 825 为分母；语义正确率以成功语义判定为分母。未判保留为未知；逐题表与统计计数可复算。纯 RAG 不使用构图模型，因此所有模型共用同一个 RAG 基线。

历史直接图每段最多 3 条关系，历史校正图最多 8 条，且批处理方式不同；全量检索两者比较包含覆盖／预算变化，不是纯粹校正消融。上面的 64 段三方式对照才具有同预算。
新增的 35B 代表性对照显示：独立图检索的有据正确率为 37.33%，直接图＋RAG 为 62.79%，校正图＋RAG 为 61.33%。加入原文检索明显改善了该图检索对照，但强 RAG 自身已达到 63.15%；因此这项结果证明原文检索的重要性，不能据此声称图给强 RAG 带来了额外收益。

| 方法 | 基线 | 指标 | 有效配对 | 差值 | 95% 区间 | 排除未判 |
| --- | --- | --- | --- | --- | --- | --- |
| 校正 KG＋RAG · Qwen 27B Dense | 直接 KG＋RAG · Qwen 27B Dense | 有据正确 | 819 | +1.83 pp | [-0.24, +3.91] pp | 6 |
| 校正 KG＋RAG · Qwen 27B Dense | 直接 KG＋RAG · Qwen 27B Dense | 证据可答 | 821 | +1.71 pp | [+0.12, +3.29] pp | 4 |
| 校正 KG＋RAG · Qwen 27B Dense | 直接 KG＋RAG · Qwen 27B Dense | 证据有用 | 821 | +0.12 pp | [-1.10, +1.34] pp | 4 |
| 直接 KG＋RAG · Qwen 27B Dense | 强 RAG | 有据正确 | 819 | -1.47 pp | [-3.30, +0.37] pp | 6 |
| 直接 KG＋RAG · Qwen 27B Dense | 强 RAG | 证据可答 | 821 | -0.61 pp | [-1.95, +0.73] pp | 4 |
| 直接 KG＋RAG · Qwen 27B Dense | 强 RAG | 证据有用 | 821 | +0.61 pp | [-0.37, +1.58] pp | 4 |
| 校正 KG＋RAG · Qwen 35B / A3B | 直接 KG＋RAG · Qwen 35B / A3B | 有据正确 | 819 | -1.47 pp | [-3.66, +0.73] pp | 6 |
| 校正 KG＋RAG · Qwen 35B / A3B | 直接 KG＋RAG · Qwen 35B / A3B | 证据可答 | 821 | +0.61 pp | [-0.97, +2.19] pp | 4 |
| 校正 KG＋RAG · Qwen 35B / A3B | 直接 KG＋RAG · Qwen 35B / A3B | 证据有用 | 821 | -0.12 pp | [-1.22, +0.97] pp | 4 |
| 直接 KG＋RAG · Qwen 35B / A3B | 强 RAG | 有据正确 | 821 | -0.37 pp | [-2.44, +1.58] pp | 4 |
| 直接 KG＋RAG · Qwen 35B / A3B | 强 RAG | 证据可答 | 822 | +0.49 pp | [-0.85, +1.82] pp | 3 |
| 直接 KG＋RAG · Qwen 35B / A3B | 强 RAG | 证据有用 | 822 | +0.24 pp | [-0.85, +1.34] pp | 3 |
| 校正 KG＋RAG · Qwen 122B / A10B | 直接 KG＋RAG · Qwen 122B / A10B | 有据正确 | 819 | -0.12 pp | [-2.20, +1.95] pp | 6 |
| 校正 KG＋RAG · Qwen 122B / A10B | 直接 KG＋RAG · Qwen 122B / A10B | 证据可答 | 821 | +1.10 pp | [-0.24, +2.56] pp | 4 |
| 校正 KG＋RAG · Qwen 122B / A10B | 直接 KG＋RAG · Qwen 122B / A10B | 证据有用 | 821 | +0.85 pp | [-0.49, +2.19] pp | 4 |
| 直接 KG＋RAG · Qwen 122B / A10B | 强 RAG | 有据正确 | 820 | -0.85 pp | [-2.80, +0.98] pp | 5 |
| 直接 KG＋RAG · Qwen 122B / A10B | 强 RAG | 证据可答 | 822 | +0.12 pp | [-1.34, +1.58] pp | 3 |
| 直接 KG＋RAG · Qwen 122B / A10B | 强 RAG | 证据有用 | 822 | -0.73 pp | [-1.82, +0.36] pp | 3 |
| 校正 KG＋RAG · Qwen 397B / A17B | 直接 KG＋RAG · Qwen 397B / A17B | 有据正确 | 803 | -1.00 pp | [-3.24, +1.12] pp | 22 |
| 校正 KG＋RAG · Qwen 397B / A17B | 直接 KG＋RAG · Qwen 397B / A17B | 证据可答 | 809 | +0.49 pp | [-0.87, +1.98] pp | 16 |
| 校正 KG＋RAG · Qwen 397B / A17B | 直接 KG＋RAG · Qwen 397B / A17B | 证据有用 | 809 | +0.99 pp | [-0.12, +2.10] pp | 16 |
| 直接 KG＋RAG · Qwen 397B / A17B | 强 RAG | 有据正确 | 802 | +1.12 pp | [-1.12, +3.37] pp | 23 |
| 直接 KG＋RAG · Qwen 397B / A17B | 强 RAG | 证据可答 | 809 | +0.49 pp | [-0.99, +1.98] pp | 16 |
| 直接 KG＋RAG · Qwen 397B / A17B | 强 RAG | 证据有用 | 809 | -0.12 pp | [-1.24, +1.11] pp | 16 |
| 直接 KG＋RAG · Qwen 35B / A3B | 直接 KG 独立检索 · 35B | 有据正确 | 815 | +25.28 pp | [+21.96, +28.71] pp | 10 |
| 直接 KG＋RAG · Qwen 35B / A3B | 直接 KG 独立检索 · 35B | 证据可答 | 817 | +32.56 pp | [+29.01, +36.11] pp | 8 |
| 直接 KG＋RAG · Qwen 35B / A3B | 直接 KG 独立检索 · 35B | 证据有用 | 817 | +31.21 pp | [+27.91, +34.52] pp | 8 |
| 校正 KG＋RAG · Qwen 35B / A3B | 直接 KG 独立检索 · 35B | 有据正确 | 811 | +23.80 pp | [+20.22, +27.25] pp | 14 |
| 校正 KG＋RAG · Qwen 35B / A3B | 直接 KG 独立检索 · 35B | 证据可答 | 815 | +32.88 pp | [+29.33, +36.32] pp | 10 |
| 校正 KG＋RAG · Qwen 35B / A3B | 直接 KG 独立检索 · 35B | 证据有用 | 815 | +30.80 pp | [+27.48, +34.11] pp | 10 |


## 模型要多大

当前证据支持把 27B 作为已验证的构图起点，而不是“最低必须 27B”的结论。它在较大模型中具有接近的支持率；122B／397B 在某些方式下覆盖更多参考命题，但没有显示稳定的全量检索优势。35B 是总参数 35B、激活约 3B 的 MoE，不能当作 3B Dense 模型比较；其余 MoE 同理。

DeepSeek 1.5B 在本协议下没有保留事实，涉及结构输出、关系协议和推理输出预算；这限制的是该模型／接口／协议组合。Cloudflare 的 Llama 1B、3B、8B 原始配置已按相同来源和预算完成三方式及语义审计；独立 Schema 适配结论仍待补测完成。8B 使用 FP8 且为 Llama 3.1，1B／3B 为 Llama 3.2，不能将代际和量化差异全部归因于参数量。

小模型的接口限制必须与事实能力分开。1B 的 64 段直接抽取没有返回所需结构；8B 的 64 段中 40 段超过八条上限而被完整拒绝。因此没有保留关系不能解释为模型读不懂原文。实际 API 探测发现 1B 明确拒绝 JSON Schema，而 3B 和 8B 可以返回符合结构的结果。新增两组独立冻结的 Schema 补测，保持原文、提示、关系词表和输出预算一致，并由各模型自己构建初始图。它是在观察原实验格式失败后追加的探索性接口适配，不替换原结果；试探用的首段来源仍在样本内，不冒充独立留出验证。

| 小模型配置 | 方式 | 构建记录 | 有事实来源 | 非 JSON／外层格式错 | 超八条来源 | 未正常完成 | 保留事实 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Llama 3.2 1B | 直接 KG | 64/64 | 0 | 64 | 0 | 0 | 0 |
| Llama 3.2 1B | KG＋原文校正 | 64/64 | 0 | 63 | 0 | 0 | 0 |
| Llama 3.2 1B | KG＋RAG＋原文校正 | 64/64 | 0 | 64 | 0 | 0 | 0 |
| Llama 3.2 3B | 直接 KG | 64/64 | 11 | 0 | 11 | 13 | 19 |
| Llama 3.2 3B | KG＋原文校正 | 64/64 | 9 | 34 | 1 | 1 | 10 |
| Llama 3.2 3B | KG＋RAG＋原文校正 | 64/64 | 7 | 36 | 1 | 1 | 11 |
| Llama 3.1 8B FP8 | 直接 KG | 64/64 | 19 | 2 | 40 | 1 | 112 |
| Llama 3.1 8B FP8 | KG＋原文校正 | 64/64 | 37 | 0 | 22 | 1 | 193 |
| Llama 3.1 8B FP8 | KG＋RAG＋原文校正 | 64/64 | 35 | 0 | 23 | 3 | 183 |

格式表是接入诊断，不是语义精度。合法空集合与失败分开；保留事实还需通过来源定位和关系协议检查。Schema 配置在三方式及审计全部完成前不发布适配排名。

原始小模型队列已完成。1B 三种方式均未保留事实；3B 的直接／原文校正／上下文校正分别只保留 19／10／11 条事实，其中来源支持 5／3／2 条。8B 分别保留 112／193／183 条，来源支持 56／114／104 条。原文校正增加了 8B 的有据事实，但原始接口配置的准确性与覆盖仍不足以替代本轮较大模型配置；是否能通过 Schema 适配改善，需等独立补测完成。

引文和端点的字面检查用于来源账本定位及结构准入；语义质量评审仍允许同义表达与合理推断。失败提案的格式、条数、谓词和引用问题另见 [协议诊断](../.cache/kg-complete-2026-10-21/protocol-diagnostics.json)，该诊断不改动原评分。

四个较大模型已通过实际 ExtractionClient → 校验器 → LightRAG 映射器的双格式合成烟测（8/8）。这证明接口与结构链路适配，不能替代真实原文的事实质量验证。Cloudflare 当前通过的是实验传输适配；尚未证明其直接配置到正式 ExtractionClient 的所有格式均兼容。

## 测试、资源与费用

- 真实存储常规回归：1008 passed, 4 skipped, 0 failures, 0 errors，5989.38 秒（最终 JUnit）
- 物理后端串行演练：2 passed, 0 skipped, 0 failures, 0 errors，153.29 秒（最终 JUnit）
- 本轮 KG 实验相关单元测试 37 项通过，相关代码 Ruff 检查通过。前轮全仓 Ruff 与 mypy（241 个源文件）检查通过；本轮未修改正式服务代码。
- 已清理用户授权的旧 domain-kg-dev 测试卷与无用构建缓存，重建 MongoDB 副本集、Neo4j、Qdrant、对象存储；未删除无关容器或镜像。
- 一次回归因 Docker 虚拟磁盘 100% 占满而中断：配置变更备份用例在准备原文时出现 BACKEND_UNAVAILABLE，随后 MongoDB 诊断文件写入失败并退出。原失败及复现日志保留。已清理约 2.66 GB 闲置构建缓存，将 MongoDB／对象存储的合成测试数据改为工作区挂载，并重新确认 rs0 主节点；内存上限不变。磁盘恢复后，配置变更和发布变更两个受影响用例均已通过（2 passed，322.53 秒）；随后另一次回归在 Qdrant WAL 写入时再次遇到磁盘满，失败记录同样保留。10 月 21 日空间回收后 Docker 曾有约 52 GiB 可用，主机约 127 GiB；受影响的备份捕获用例通过（18.45 秒），随后完整常规回归及独立物理演练均通过。10 月 23 日复查 Docker 约 27 GiB 可用；再次回收已删除块后主机约 18 GiB 可用，未删除现存数据。四项跳过分别是可选浏览器预览、医疗 PDF、整篇医疗语料及独立本地发布评测，不属于本轮公共语料 API 比较。
- 所有本地模型推理与下载已停止，部分本地结果仅存档、不计入比较。

| 阶段 | 调用尝试 | 已知标价估算 RMB | 未知费用尝试 |
| --- | --- | --- | --- |
| 已完成 v3 | 53789 | 892.9200 | 651 |
| 本次扩展 | 16018 | 703.9673 | 259 |

| Cloudflare 模型 | 尝试 | 已报告 Neurons | 等价标价 USD | 未知计量尝试 |
| --- | --- | --- | --- | --- |
| Llama 3.2 1B | 175 | 3454.11 | 0.038026 | 1 |
| Llama 3.2 3B | 186 | 3892.06 | 0.042821 | 0 |
| Llama 3.1 8B FP8 | 186 | 5351.19 | 0.058849 | 0 |

费用对应当前保存的分析快照；进行中的 Schema 调用尚未全部计入。不同货币不直接相加；标价估算不是实际扣费账单。缺失 usage、超时或无公开定价的调用归为未知，不假定免费。原始图与嵌入等早期历史费用不在已记录费用内。Cloudflare 按用户要求保持免费，达到实验日额度后暂停，次日恢复。其费用表按实际模型合并原始与 Schema 构建请求，不重复计量；独立接入和 Schema 探测合计少于 50 Neurons，另行保存，未计入该表。等价标价不是扣费。北京时间每日 08:05 已设置自动续跑检查，没有订阅或升级付费计划。价格来源：[Cloudflare 官方](https://developers.cloudflare.com/workers-ai/platform/pricing/)。

## 每个 KG 的节点图

| KG | 节点数 | 关系数 | 来源数 | 离线打开 |
| --- | --- | --- | --- | --- |
| qwen3.8-27b / 普通 KG（历史直接抽取） | 15983 | 9291 | 6566 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.8-27b--initial.html) |
| qwen3.8-27b / 图 + RAG + 原文矫正 | 35603 | 26063 | 6566 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.8-27b--corrected.html) |
| qwen3.6-35b-a3b / 普通 KG（历史直接抽取） | 14924 | 8739 | 6566 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.6-35b-a3b--initial.html) |
| qwen3.6-35b-a3b / 图 + RAG + 原文矫正 | 31877 | 23182 | 6566 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.6-35b-a3b--corrected.html) |
| qwen3.5-122b-a10b / 普通 KG（历史直接抽取） | 10222 | 6068 | 6566 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.5-122b-a10b--initial.html) |
| qwen3.5-122b-a10b / 图 + RAG + 原文矫正 | 47733 | 37396 | 6566 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.5-122b-a10b--corrected.html) |
| qwen3.5-397b-a17b / 普通 KG（历史直接抽取） | 22211 | 14538 | 6566 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.5-397b-a17b--initial.html) |
| qwen3.5-397b-a17b / 图 + RAG + 原文矫正 | 41978 | 32085 | 6566 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.5-397b-a17b--corrected.html) |
| deepseek-r1-distill-qwen-1.5b / 普通 KG（历史直接抽取） | 42 | 21 | 6566 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/deepseek-r1-distill-qwen-1.5b--initial.html) |
| deepseek-r1-distill-qwen-1.5b / 64段构图对照 / 直接构图 | 0 | 0 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/deepseek-r1-distill-qwen-1.5b--cohort64--direct.html) |
| deepseek-r1-distill-qwen-1.5b / 64段构图对照 / 原文矫正 | 0 | 0 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/deepseek-r1-distill-qwen-1.5b--cohort64--source_repair.html) |
| deepseek-r1-distill-qwen-1.5b / 64段构图对照 / 相关材料与图辅助＋原文矫正 | 0 | 0 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/deepseek-r1-distill-qwen-1.5b--cohort64--context_repair.html) |
| qwen3.8-27b / 64段构图对照 / 直接构图 | 437 | 290 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.8-27b--cohort64--direct.html) |
| qwen3.8-27b / 64段构图对照 / 原文矫正 | 413 | 269 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.8-27b--cohort64--source_repair.html) |
| qwen3.8-27b / 64段构图对照 / 相关材料与图辅助＋原文矫正 | 394 | 257 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.8-27b--cohort64--context_repair.html) |
| qwen3.6-35b-a3b / 64段构图对照 / 直接构图 | 408 | 273 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.6-35b-a3b--cohort64--direct.html) |
| qwen3.6-35b-a3b / 64段构图对照 / 原文矫正 | 378 | 248 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.6-35b-a3b--cohort64--source_repair.html) |
| qwen3.6-35b-a3b / 64段构图对照 / 相关材料与图辅助＋原文矫正 | 363 | 243 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.6-35b-a3b--cohort64--context_repair.html) |
| qwen3.5-122b-a10b / 64段构图对照 / 直接构图 | 555 | 394 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.5-122b-a10b--cohort64--direct.html) |
| qwen3.5-122b-a10b / 64段构图对照 / 原文矫正 | 605 | 428 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.5-122b-a10b--cohort64--source_repair.html) |
| qwen3.5-122b-a10b / 64段构图对照 / 相关材料与图辅助＋原文矫正 | 609 | 435 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.5-122b-a10b--cohort64--context_repair.html) |
| qwen3.5-397b-a17b / 64段构图对照 / 直接构图 | 558 | 385 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.5-397b-a17b--cohort64--direct.html) |
| qwen3.5-397b-a17b / 64段构图对照 / 原文矫正 | 553 | 380 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.5-397b-a17b--cohort64--source_repair.html) |
| qwen3.5-397b-a17b / 64段构图对照 / 相关材料与图辅助＋原文矫正 | 556 | 381 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/qwen3.5-397b-a17b--cohort64--context_repair.html) |
| cf-llama-3.2-1b / 64段构图对照 / 直接构图 | 0 | 0 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/cf-llama-3.2-1b--cohort64--direct.html) |
| cf-llama-3.2-1b / 64段构图对照 / 原文矫正 | 0 | 0 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/cf-llama-3.2-1b--cohort64--source_repair.html) |
| cf-llama-3.2-1b / 64段构图对照 / 相关材料与图辅助＋原文矫正 | 0 | 0 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/cf-llama-3.2-1b--cohort64--context_repair.html) |
| cf-llama-3.2-3b / 64段构图对照 / 直接构图 | 27 | 19 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/cf-llama-3.2-3b--cohort64--direct.html) |
| cf-llama-3.2-3b / 64段构图对照 / 原文矫正 | 19 | 10 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/cf-llama-3.2-3b--cohort64--source_repair.html) |
| cf-llama-3.2-3b / 64段构图对照 / 相关材料与图辅助＋原文矫正 | 16 | 11 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/cf-llama-3.2-3b--cohort64--context_repair.html) |
| cf-llama-3.1-8b-fp8 / 64段构图对照 / 直接构图 | 141 | 112 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/cf-llama-3.1-8b-fp8--cohort64--direct.html) |
| cf-llama-3.1-8b-fp8 / 64段构图对照 / 原文矫正 | 238 | 193 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/cf-llama-3.1-8b-fp8--cohort64--source_repair.html) |
| cf-llama-3.1-8b-fp8 / 64段构图对照 / 相关材料与图辅助＋原文矫正 | 231 | 183 | 64 | [HTML](/Users/zachary/.codex/visualizations/2026/10/20/01a15a34-b69d-7b52-92ec-9ff479bd707f/outputs/01a15a34-b69d-7b52-92ec-9ff479bd707f/kg-graphs/cf-llama-3.1-8b-fp8--cohort64--context_repair.html) |

HTML 包含全部节点、边和原文，无 CDN 依赖。搜索或点击节点可看局部邻域与引用，支持平移、缩放、JSON 下载和当前视图 PNG 导出。为保持浏览流畅，每次最多画 180 个节点，完整图数据没有截掉。实体按来源隔离，橙色词面连接只是未确认的检索提示，不能视为实体合并或新事实。

图导出的计数、端点、来源引文与内嵌 JSON 已校验；已有图查看器通过 DOM／canvas 模拟环境的搜索、桥接、空查询和数据一致性检查，代表图静态预览已检查。此前浏览器使用被禁用；本次再次尝试时，浏览器安全策略阻止 file: 本地文件地址。未绕过限制，因此不声称完成真实浏览器视觉验收。

数据与署名：SQuAD、HotpotQA、DROP 使用 CC BY-SA 4.0；MuSiQue 使用 CC BY 4.0。上游链接、固定下载版本和校验记录随图包交付。公开原文仍保留来源身份，图是模型构建提案，不是平台正式发布事实。

## 复核入口

- [原文校正与检索主分析](/Users/zachary/Workspace/agentos/mcp-servers/domain-kg/.cache/kg-complete-2026-10-21/analysis-progress.json)
- [冻结协议](/Users/zachary/Workspace/agentos/mcp-servers/domain-kg/.cache/kg-complete-2026-10-21/frozen.json)、[Cloudflare 补充协议](/Users/zachary/Workspace/agentos/mcp-servers/domain-kg/.cache/kg-complete-2026-10-21/cloudflare-supplement-frozen.json)
- [解析修复声明](/Users/zachary/Workspace/agentos/mcp-servers/domain-kg/.cache/kg-complete-2026-10-21/inventory-recovery-amendment.json)、[固定原文定性检查](/Users/zachary/Workspace/agentos/mcp-servers/domain-kg/.cache/kg-complete-2026-10-21/native-audit-notes.json)、[小模型定性检查](/Users/zachary/Workspace/agentos/mcp-servers/domain-kg/.cache/kg-complete-2026-10-21/native-audit-cloudflare-notes.json)
- [已完成五模型及全量检索重放校验](/Users/zachary/Workspace/agentos/mcp-servers/domain-kg/.cache/kg-complete-2026-10-21/verification-base-and-retrieval.json)：960 条构建记录、5,000 个新增问题—方法组合、4,912 个独立评测结果；不包含未完成 Cloudflare 构图审计。
- [原始八模型与全量检索重放校验](/Users/zachary/Workspace/agentos/mcp-servers/domain-kg/.cache/kg-complete-2026-10-21/verification-original-and-retrieval.json)：单独验证已完成原始配置；Schema 补测仍须最终全矩阵校验。
- [构图重放校验](/Users/zachary/Workspace/agentos/mcp-servers/domain-kg/.cache/kg-complete-2026-10-21/construction-verification.json)；最终全矩阵校验只有 verification.json 通过后才宣称完整。
