# 远端小模型接入与预检（2026-11-02）

已将 1.5B / 7B 接入 bbio 的现有 Ollama，模型不在本机运行。API 连通性、回答格式和生产抽取分别报告；本轮不是检索/回答质量比较，不能声称 KG 带来提升。

## 免费在线服务核查

此前项目实际使用的免费补充服务是 Cloudflare Llama 1B、3B、8B，不能代替要求的 1.5B、7B。原临时 Cloudflare token 已不存在，本次没有重跑这些型号。
硅基流动官方公告确认 Qwen2-1.5B 和 DeepSeek-R1-Distill-Qwen-1.5B 已在 2025-06-05 下线，Qwen2-7B 在 2026-03-17 下线，DeepSeek 蒸馏 7B 在 2026-04-29 下线。其旧教程仍有 Qwen2.5-7B 免费用法，但本次没有已配置的硅基流动凭据，未验证当前账号价格或推理权限；不能据此宣称当前免费可用，也不宣称所有免费端点都不存在。

- [硅基流动官方公告](https://docs.siliconflow.cn/docs/release-notes/overview)
- [官方旧版免费调用教程](https://docs.siliconflow.cn/docs/usercases/use-siliconcloud-in-bob)
- [Cloudflare 模型变更记录](https://developers.cloudflare.com/workers-ai/changelog/)

## 已配置的 bbio 服务

- 复用 `offline-model-ollama`，Ollama 0.5.7；保留原服务，未重启或开放公网端口。
- bbio：Xeon Gold 5218，64 个逻辑 CPU，约 125 GiB RAM；容器无 GPU 映射，使用 CPU。
- 7B 复用已有 4,683,087,332-byte 模型；新增 1.5B 模型 986,061,892 bytes，下载发生在服务器。
- 两档均 Q4_K_M；模型 tag、完整摘要及参数规模固定在 `eval/kg_quality_bbio.py`。
- `127.0.0.1:29434` 是 SSH 转发至 bbio 的 `127.0.0.1:11434`，不是本机推理服务。
- 预检结束后关闭本次临时隧道；再次运行前执行 `ssh -N -L 127.0.0.1:29434:127.0.0.1:11434 bbio`，保持该 SSH 进程运行。
- 原容器没有模型卷挂载；新增模型保存在该容器的可写层，重建容器前需要保留模型。
- 登录密码未写入代码/配置/实验记录。阿里云 API key 不发送到 bbio。

## 同一协议的真实调用

沿用前次审计的 717 字符短证据、13,993 字符长证据及 236/457/1,676 字符三个抽取窗口。messages、评分规则、回答 2,048 token 与抽取 8,192 token 上限不变；原生 API 显式设置 16,384 上下文、8 CPU threads、temperature=0。
远端每次调用最多 600 秒且串行；阿里云原有 reader 180 秒/抽取 120 秒不变。硬件、量化、冷启动和等待上限不同，延迟不能解释成纯模型规模效应。

| 工作模型 | 短回答协议 / 秒 | 长回答协议 / 秒 | 短、中、长抽取通过数 | 保留关系：短 / 中 / 长 |
| --- | --- | --- | --- | --- |
| `bbio-qwen2.5-1.5b-q4_k_m` | 通过 / 14.8 | 未通过 / 61.7 | 0/3 | 失败 / 失败 / 失败 |
| `bbio-qwen2.5-7b-q4_k_m` | 通过 / 53.7 | 通过 / 265.2 | 3/3 | 2 / 14 / 2 |

| 工作模型 | 短抽取秒 | 中抽取秒 | 长抽取秒 | API 次数：短 / 中 / 长 |
| --- | ---: | ---: | ---: | --- |
| `bbio-qwen2.5-1.5b-q4_k_m` | 22.7 | 46.1 | 512.4 | 1 / 1 / 1 |
| `bbio-qwen2.5-7b-q4_k_m` | 127.4 | 302.9 | 707.8 | 1 / 1 / 2 |

抽取秒数包含原协议的一次证据纠正（若触发）；600 秒限制针对单次 API 调用，不能把两次调用合计误作一次请求。

1.5B 长回答没有提供必需引用；短/中抽取遗漏实体 refs 等字段。最长抽取 HTTP 200，但反复生成重复字段，用尽 8,192 token 后以 done_reason=length 截断（512.3 秒）。该调用的生产异常摘要是通用的 provider unavailable，原始记录明确表明这是输出截断，不是网络不通。保留失败，未填补引用或降低验证要求。协议失败与语义错误分开，未运行固定裁判的回答不能自动记作错误或零分。合法 batch（尤其空 batch）也不能证明事实语义正确。

7B 最长样本首次输出引用编号不合法，按既定协议执行一次纠正；最终 batch 带有 extraction_rejected_assertions:11 警告，只保留 2 个节点、2 条 negative 关系。手工核对发现其中 Michael Pittman → injured and replaced by → Buccaneers 无法表达原文受伤球员由 Earnest Graham 替补的事件，另一条 negative injured 也不能忠实表达该受伤事件。这是具体的语义反例，不是全量边精度评分；格式通过不能被解释成构图有效。

7B 在接入前还做过一次独立短/长输入诊断，均通过，耗时 68.4/267.3 秒；这些响应保留在 `readers/`，没有冒充适配器的新请求。

## 验证与未完成项

- 116 项相关测试通过；Ruff 通过；生产 profile 类型检查通过。
- 两档模型的 754/754 窗口均通过保守请求预算检查；这不等于事实质量通过。
- 新冻结目录 `.cache/kg-quality-v4-frozen` 保留同一 23 题、747 段、754 窗口及语义指标；v3 原始文件保留。
- 每档独立构图并用该档回答；固定阿里云 embedding 和 397B 裁判不变。
- 本轮不重测大模型、embedding 或裁判；此前证据见 `docs/api-readiness-2026-11-02.md`。旧裁判响应离线重放仍为 12/12，通过记录见 `judge-replay.json`，不是新 API 调用。
- **全矩阵仍未就绪**：小模型协议失败不能忽略；先前已复现的整段否定词校验误删问题也尚未修复。未执行全量建图、检索或 QA 质量比较。
- API/协议预检不是语义准确率评估。此处的关系数是通过当前生产校验的观察数，不是人工确认的正确事实数。

## 复核文件

完整调用与输出：`.cache/small-model-readiness-2026-11-02/`；`adapter/manifest.json` 固定代码/输入，`summary.json` 汇总，`receipt.json` 对原始 JSON 文件逐一散列。
`probe.py` 是串行真实预检脚本；`adapter/initial-shape-diagnostic.json` 记录部分失败样本的 Pydantic 字段诊断。`structure/<model>/<sample>/statistics.json` 保存每个预检窗口的节点/关系、孤立节点、分量、类型与失败统计；其语料分母仅为该窗口的来源，不能当成全库指标。
