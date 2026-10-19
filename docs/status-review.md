# 当前进度 / Current progress

核对日期 / Reviewed: **2026-10-19（Asia/Shanghai）**。

最新完成 40 道未参与优化的新题对照：4B 纯向量和双路径经语义复核均答对
34/40（85%）；引用完整率分别为 31/36（86.1%）、29/36（80.6%）。双路径未达到
用户确认的 90% 引用完整率门槛，未启动整篇 PDF 构建。68 次实际模型请求全部成功，
没有重试或继续调参；419 项 unit/protocol 检查通过，自有模型已正常退出，生产
代码和镜像不变。详见 [4B 新题报告](4b-holdout-report.md)。

此前 4B 双路离线试验已结束，含准备/诊断共 **5 次未达到完整引用标准后停止**。
最终探索版在 10 道暂定题上，向量＋关键词答案匹配 8/10，增加轻量检索图后 10/10；
但两道多跳题漏引中间来源，严格“答对且引用完整”仍为 8/10。复核事实关系未增加收益。
这不是独立 held-out 或真实 KG 发布验收；410 项 unit/protocol 检查通过，自有模型
已关闭，生产源码和镜像不变。详见 [4B 双路试验](hybrid-retrieval-report.md)。

此前的抽取实验：按用户澄清采用迭代优化，累计 **5 次未达标后已停止**。最佳紧凑协议
在两个开发片段的 3 条暂定目标关系中找回 2 条，另有 1 条错误；同模型多轮核验未
可靠改善结果。4B 该次超时不能用于语义评分。385 项工程检查通过，所有自有模型
已停止，未发布图谱或改动生产抽取链路。详见
[五次迭代汇总](iterative-extraction-report.md)。Task 9 质量门禁继续保留。

**当前配置下的本地平台收尾已完成，Linux/arm64 应用镜像已交付。**
本轮补齐退役/清除来源的人工历史恢复、连续备份恢复、未完成备份清理及旧索引回收。
清理要求写入确认结束且无活跃工作或保留读取；状态不明及旧版未跟踪写入继续保留。
当前索引、独立证据、人工修订及已删除原文的隔离边界均有真实存储回归。

最终默认 pytest 清单按审计分组核验：**727 通过、6 项显式跳过、0 项未解决失败**，
共 733 项。测试观察器隐藏原生函数签名的问题已修正，失败项和全部尚未执行项均
重跑通过；应用源码不变。另行启用的当前版本检索评估、浏览器预览、物理配置恢复/
修复各 1 项通过（112.83、89.89、126.53 秒）。Ruff、严格 mypy（239 模块）、前端
4 项检查与分发包边界检查通过。详见 [验收清单](release-checklist.md)。

检索评估使用 12 个受控问题、五种模式，验证 41 条精确原文引用，越权结果及真实
模型调用均为 0。物理恢复演练结束后已还原主配置；浏览器预览已正常结束。四个
数据库仍受合计 **1.476 GB** 硬上限约束，swap 禁用。镜像通过默认启动命令、
非 root/只读/无网络、原生 PDF/文本解析与未认证 MCP 拒绝检查，已导出并重新加载。
镜像标签 `domain-kg:local`，应用版本 `02a23d8`；最终验收版本 `0856abee` 的应用
源码与镜像一致。

证据与本地交付物（均不入 Git）：`.cache/local-release.json`、
`.cache/verification/regression-report.json`、`.cache/domain-kg-local-image.tar`。
运行方式及归档校验见 [镜像说明](deployment.md)，检索边界见
[评估报告](evaluation-report.md)，本轮范围见
[本地收尾计划](superpowers/plans/2026-10-18-local-platform-completion.md)。

用户明确将模型/Schema 升级功能、外部部署及跨环境迁移留待后续确定。
Task 9 真实抽取质量、真实 OIDC/目标 MCP 客户端、真实语料人工团队试用和规模性能
验收继续单独保留；本地受控验收不证明这些门禁通过。原始 Task 18/19 仍含上述更广
范围，因此主计划复合勾选不变。

**本轮真实抽取仍未完成 / Real extraction is still incomplete.**
模型和分块修复已提交 `b0dea0c`、`b4074e0`：服务端生成来源坐标、按上下文合并
文本行、保存逐块断点、仅对输出截断做有上限的再分块，并记录拒绝响应的用量。
医学类型表与命名端点协议、来源过滤的中英图谱总览和 API 配置已准备。

用户批准的 Qwen3-4B Q4_K_M 已下载并校验（2,497,280,256 字节）。经过格式诊断，
最终三段检查仅 **1/3 通过**，另两段因来源或结构问题被拒；还观察到原文不支持的
分类关系，故不能把来源对应检查当成语义准确性。最终峰值 RSS 3.082 GB，低于单独
批准的 3.5 GB 监测上限，模型已停止。没有运行整篇模型抽取或发布真实 KG。

The approved 4B model's final three-passage gate passed only one case. Two were
rejected, and inspection also found unsupported semantic claims. The owned model
stopped after a 3.082 GB peak. The full-document run remains gated; no genuine
full-PDF graph has been published. There is no substitute simulated result.

等待可用 API endpoint、抽取模型名，以及私有文件中的 key：
`/Users/zachary/.cache/domain-kg/providers.medical-api.local.json`（0600）。
配置模板为 [medical API profile](../config/providers.medical-api.example.json)。
远程连接未启用。下一阶段是实际 API 小样本验收、全 PDF 抽取/发布与持久页面验收。
全量运行脚本仍属于未验收准备代码，不应报告为已完成。

主计划 19 阶段中，14 阶段的原始本机验收已勾选。Task 18/19 的当前配置本地范围
本轮已验证；原始任务中的升级、部署及真实团队评估等更广门禁仍保留。第 3、9、15
阶段仍有真实身份、模型和客户端外部验收。本地验收不代表可直接上线。

The current-configuration local scope is verified. Original composite Task 18/19
checkboxes remain open for broader deferred gates; Tasks 3, 9 and 15 retain real
identity/model/client acceptance.
本次关系图补充已完成真实医学 PDF 回归：NHS 文件 412,271 字节、7 页，
228 个原始文本块、15 个处理窗口、257 处原文定位通过。图谱为明确的受控抽取测试，
含 2 节点、1 关系，实际模型调用 0 次，医学抽取质量门禁保持未完成。
来源关系可打开对应的原文版本，人工关系继续查看人工修订历史。

**纠正 / Correction:** 上述 2 节点、1 关系来自只匹配 `diabetes` 和 `annual review`
的受控测试响应，**不是整篇 PDF 的知识抽取结果**。解析 7 页不等于抽取了 7 页的知识。
These two nodes and one edge are a controlled pipeline fixture, not the PDF's extracted KG.

2026-10-18 补做真实 0.8B 模型检查：第 4 页 203 个字符、1 次请求、213 tokens、
3.527 秒；原生峰值 RSS 872,611,840 字节，结束后关闭自有服务。紧凑试验响应的
5 个实体仅 1 个通过逐字段原文对应检查，关系为 0，整个批次被现有来源校验拒绝，
发布次数为 0。该试验格式尚未接入正式 provider。现有完整格式在保守的 4096 上下文
预算下连一个原文字符也无法容纳。**真实医学 PDF 图谱仍未完成**；未扩大语料或
下载更大的模型。医学预览已增加醒目的中英双语说明；相关 12 项检查通过（21.30 秒）。

A real, single-request 0.8B probe on 203 characters from page 4 failed canonical
evidence validation: one of five entity names matched its cited passage; no
relations were returned and nothing was published. It used 213 tokens in 3.527 s,
with 872.6 MB peak native RSS. The compact experimental format is not integrated
into the provider; the existing full format cannot fit under the conservative
4096 context budget. **Full medical-PDF KG extraction remains unverified.**

[本轮医学关系图预览 / Medical graph preview](http://localhost:28080/__smoke__/start/owner?lang=zh-CN)
（隔离预览存活约 10 分钟；页面顶部切换 English；此精简模式不运行编辑工作进程）。

Real NHS PDF smoke passed with seven pages, 15 fitted chunks and 257 verified
source spans. Extraction/embedding responses are controlled fixtures; the two-node
example does not establish model or clinical quality.

完整阶段记录见 [implementation progress](implementation-progress.md) 与
[implementation plan](implementation-plan.md)。

节点详情页已接入 neovis.js 节点关系图，可拖动、缩放、选择查看节点与关系详情。
仅使用当前权限可见的一跳邻域，最多 25 节点 / 40 条关系，保留来源、人工及未验证
导入的区别，并提供键盘可用的文字链接。此前已在实际浏览器观察总览 canvas：
受控样本的 2 节点、1 关系正常绘制。运维页的未完成备份选项已观察并选择，未在
浏览器提交清理。预览正常退出，1 项真实存储预览检查通过（270.61 秒）。截图在
本地 `.cache/local-graph.png`。最终当前版本预览另行通过（89.89 秒），已观察并选择
旧索引回收选项；最新 `.cache/local-operations.png` 为该次截图。未在浏览器提交
清理；文件选择器仍未验收，上传链路已有真实存储 HTTP/表单自动化验证。

The bounded neovis.js overview rendered two synthetic nodes and one relationship
in the real browser. The operations selection was observed without submitting a
destructive browser action. The isolated preview exited cleanly. File-picker
acceptance remains open.

## 本轮完成 / Completed in this run

| 内容 / Feature | 结果 / Evidence |
| --- | --- |
| 管理界面中文 / English 切换 | 全部模板的导航、表单、状态、无障碍标签及上传反馈使用本地词表；不调用翻译模型。原文、名称与编辑理由不变。 / Local interface translations preserve original evidence. |
| 语言设置与权限边界 / Locale boundaries | 请求隔离、浏览器语言权重、偏好 cookie、安全返回路径、原始内容转义、错误页不回显被拒路径。309 项单元及真实数据库控制台检查通过，24.36 秒。 / 309 unit and real-store console checks passed. |
| 实际双语预览 / Live bilingual preview | 8 个中文页面、英文切换、原始证据及 reader 运维拒绝通过 HTTP 检查；隔离预览正常退出，126.26 秒。 / Eight Chinese pages and English switching verified through HTTP. |
| 检索索引更新任务 / Search index update jobs | 当前 owner 专属，显示待处理、失败、需修复数量，以及采样中最早待处理时间。超过 1,000 个任务时计数明确为下限。图谱/向量索引可用性继续单独核验。 / Bounded owner-only observability, separate from receipt readiness. |
| 指标验证 / Metrics verification | 11 项定向检查通过，16.31 秒，覆盖真实 MongoDB 隔离、溢出、读取后撤权、安全错误和双语页面。308 项完整单元检查通过，6.64 秒。Ruff、严格 mypy 通过。 / Focused storage/API checks and full unit regression passed. |

具体范围：[双语阶段](superpowers/plans/2026-10-18-bilingual-console.md)、
[投影指标阶段](superpowers/plans/2026-10-18-projection-work-metrics.md)。

## 已可查看 / Available functionality

- 多组织、多图谱、成员/组和材料 ACL；材料原件、处理任务、替换、排除、删除与恢复。
  / Organizations, KGs, memberships, scoped access and source lifecycle.
- 有来源的快速/深度检索、引用、节点关系、人工修订、合并/拆分及补偿回滚。
  / Evidence retrieval, citations, curation, identity changes and compensating rollback.
- 备份、恢复到新图谱、投影修复、同配置索引重建、到期文件包及失败恢复目标清理。
  / Backups, fresh-namespace restores, repair, current-configuration index rebuilds and cleanup.
- 每日预算、7 天 provider 用量历史、任务指标与 owner 操作审计；HTTP 历史可选最多 31 天。
  / Daily provider budgets, bounded history, work metrics and owner audit.

已经完成的小型真实演练包括：代际重建后备份恢复；合并/拆分历史恢复后的继续编辑；
不同物理图/向量配置间的串行恢复和真实缺失向量修复。最新复验 126.53 秒，结束后恢复
原配置。Task 18 指定的备份/配额/审计回归 14 项通过，203.77 秒。

Verified drills include generation-aware backup/restore, restored split history with
follow-up edits, and serial recovery/repair on separate physical graph/vector volumes.
The final physical drill passed in 126.53 seconds and restored the primary configuration.
The required backup/quota/audit regression passed 14 cases in 203.77 seconds.

## 本轮之外的门禁 / Gates outside this pass

| 范围 / Area | 仍需完成 / Remaining |
| --- | --- |
| Task 18 历史完整性 | 当前本地范围已通过历史链和最终组合回归；不能证明安全的输入继续拒绝。 / Current local history scope verified. |
| Task 18 清理与升级 | 当前配置的旧代际与未完成备份回收已验证；旧版或状态不明写入保留。模型/Schema 升级和跨环境迁移按用户要求后续再定。 / Local collection verified; upgrades and migration deferred. |
| 部署验收 / Deployment | 本机 Linux/arm64 镜像与离线解析隔离已通过。外部最小权限配置、其他架构与远程 CI 留待环境确定；未推送或部署。 / Local image passed; external deployment acceptance is deferred. |
| Task 19 评估 / Evaluation | 本地 held-out 五模式及多图谱权限/生命周期自动化回归已通过。真实语料独立人工题集、外部基线、人工团队试用和规模性能仍单独保留。 / Local platform checks passed; real-corpus/human/scale acceptance remains open. |
| 外部门禁 / External gates | 真实 OIDC 登录、真实模型完整来源跨度抽取质量、外部 MCP Inspector/目标客户端；浏览器文件选择器操作仍未验收。 / Real IdP, grounded extraction and external-client acceptance. |

0.8B 小模型通过过极小事实检查，但未通过完整来源跨度抽取，不能记为抽取质量通过。
Tiny local facts passed on the 0.8B model; full grounded extraction did not pass.

## 本机资源与预览 / Laptop resources and preview

Docker 数据库配置与运行硬上限均为 **1.476 GB**，禁用 swap，低于 1.5 GB。
此前受控回归的实际模型调用为 **0**；本轮原生模型检查结果见本文顶部。
原生模型预算与 Docker 分开测量。
配置详见 [laptop smoke](laptop-smoke.md) 和 [operations](operations.md)。

Docker hard limits total **1.476 GB**, with swap disabled. The earlier controlled
pipeline run made zero actual model calls; see the new native checks above.
The native model budget is separate.

以下入口仅在手动启动的隔离测试预览存活期间有效（默认 10 分钟）。测试登录不进入
正式应用，不代表外部 OIDC 通过；过期后需重启预览。新预览使用已提交的冻结源码。
These links work only while the isolated, ten-minute preview is running; its test
sessions are absent from the production application.

- [中文 owner 运维 / Chinese owner operations](http://localhost:28080/__smoke__/start/owner?view=operations&lang=zh-CN)
- [English owner operations](http://localhost:28080/__smoke__/start/owner?view=operations&lang=en)
- [中文检索 / Chinese search](http://localhost:28080/__smoke__/start/owner?view=search&lang=zh-CN)
- [Reader](http://localhost:28080/__smoke__/start/reader)
- [提前结束 / Finish preview](http://localhost:28080/__smoke__/finish)
