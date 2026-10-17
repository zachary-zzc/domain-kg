# 当前进度 / Current progress

核对日期 / Reviewed: 2026-10-18。

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

主计划 19 阶段中，14 阶段已完成本机验证。第 18、19 阶段仍有开发与验收工作，
第 3、9、15 阶段仍需外部验证。不能把当前演示或局部测试等同于可上线。

14 of 19 stages have passed local verification. Tasks 18–19 remain in progress;
Tasks 3, 9 and 15 retain external gates. The demo does not establish release readiness.
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
导入的区别，并提供键盘可用的文字链接。当前会话关闭了浏览器自动化；已经验证
脚本契约与真实数据库页面权限，尚未自动观察实际 canvas 绘制与鼠标操作。

Node pages now embed a bounded neovis.js relationship graph. Script contracts and
real-store page permissions passed; the rendered canvas still needs visual confirmation
because browser automation is disabled in this session.

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
不同物理图/向量配置间的串行恢复和真实缺失向量修复。后者 119.53 秒，结束后恢复
原配置。Task 18 指定的备份/配额/审计回归 14 项通过，203.77 秒。

Verified drills include generation-aware backup/restore, restored split history with
follow-up edits, and serial recovery/repair on separate physical graph/vector volumes.
The physical drill passed in 119.53 seconds and restored the primary configuration.
The required backup/quota/audit regression passed 14 cases in 203.77 seconds.

## 尚未完成 / Remaining work

| 范围 / Area | 仍需完成 / Remaining |
| --- | --- |
| Task 18 历史完整性 | 已清除或退役来源的完整权威历史、仅由人工修订依赖的来源与其他 checkpoint 链。现有重建模式会拒绝不能证明安全的输入。 / Complete retired-source and alternative ancestry coverage. |
| Task 18 清理与迁移 | 旧/未封存代际及不完整备份的收集、旧存储位置保留许可、明确的 schema/model 迁移模式。不能仅按 TTL 推断写入已结束。 / Generation/capture collection and schema/model migration extensions. |
| 部署验收 / Deployment | 外部最小权限配置、Linux 镜像与解析隔离、远程 CI 验收。未构建本机镜像、推送或部署。 / External least-privilege and Linux/CI acceptance. |
| Task 19 评估 / Evaluation | 独立 held-out 集、同预算模式/基线比较、最终适当回归与多图谱团队试用。未执行高资源性能基准。 / Held-out evaluation, comparisons and team pilot. |
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
