# 阿里云 Key 可调用模型清单：按参数规模排序

验证日期：2026-10-19，Asia/Shanghai。端点：
`https://dashscope.aliyuncs.com/compatible-mode/v1`。

本轮对 31 个新候选逐个执行短调用，并合并当天之前已测的 7 个不同候选，共覆盖
38 个模型 ID。共 11 个 ID 有实际 HTTP 200 推理结果，其中 9 个具有公开标称规模、
2 个在本次核查中未确认参数规模。不把模型列表出现名称当作调用成功。

本轮请求只发送 `Reply with OK only.`，无真实语料/医疗材料。没有启动本地模型、
下载、数据库或容器，没有部署服务。以下是接口可用性清单，不是准确率排行榜。

## 已知参数规模且实际可调用

按官方模型卡的标称语言模型总规模排序；MoE 同时列每 token 激活规模。模型名称中的
标称规模不等于精确权重计数，亦不保证 API 服务端权重精度与公开 checkpoint 相同。

| API 模型 ID | 标称总参数 | MoE 激活参数 | HTTP / 短指令结果 | 本次单次延迟 |
| --- | ---: | ---: | --- | ---: |
| qwen3.8-2.4t-a95b | 2.4T | 95B | 200，OK | 1.525 秒 |
| qwen3.5-397b-a17b | 397B | 17B | 200，OK | 1.519 秒 |
| qwen3.5-122b-a10b | 122B | 10B | 200，OK | 0.575 秒 |
| qwen3.6-35b-a3b | 35B | 3B | 200，OK | 0.496 秒 |
| qwen3.5-35b-a3b | 35B | 3B | 200，OK | 0.539 秒 |
| qwen3.8-27b | 27B | —，Dense | 200，OK | 0.545 秒 |
| qwen3.6-27b | 27B | —，Dense | 200，OK | 0.566 秒 |
| qwen3.5-27b | 27B | —，Dense | 200，OK | 0.712 秒 |
| deepseek-r1-distill-qwen-1.5b | 1.5B（标称） | —，Dense | 200，但未按指令只答 OK | 2.349 秒 |

2.4T 开启思考，其余 Qwen3.5/3.6/3.8 采用非思考模式；DeepSeek 未传不支持的思考
开关。单次延迟不能当成公平性能排名，尤其两种思考模式不同。

1.5B 蒸馏模型实际答复为“I understand your request. Please continue with your
conversation.”，证明接口可调用，但这一次简单指令未通过。其请求 `max_tokens=128`，
服务端 usage 报告 `completion_tokens=410`；保留该差异，不声称已验证严格输出预算
或结构化输出能力。没有据单次响应推断整体准确率。

参数来源：
[2.4T/A95B 官方模型卡](https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B)、
[397B/A17B](https://huggingface.co/Qwen/Qwen3.5-397B-A17B)、
[122B/A10B](https://huggingface.co/Qwen/Qwen3.5-122B-A10B)、
[3.6 的 35B/A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)、
[3.5 的 35B/A3B](https://huggingface.co/Qwen/Qwen3.5-35B-A3B)、
[3.8 的 27B](https://huggingface.co/Qwen/Qwen3.8-27B)、
[3.6 的 27B](https://huggingface.co/Qwen/Qwen3.6-27B)、
[3.5 的 27B](https://huggingface.co/Qwen/Qwen3.5-27B)、
[DeepSeek 蒸馏系列官方模型卡](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B)。

`A3B` 表示每 token 约 3B 参数激活，**35B-A3B 仍是标称 35B 总规模**，不能当成
3B Dense 小模型或等量内存的替代品。

## 实际可调用，但参数规模未确认

- `qwen3.8-flash`：当天已真实调用成功，并完成合成题对照。
- `qwen3.7-plus`：当天已真实调用成功，并完成合成题对照。

两者不插入参数排序，不猜测是 7B、14B 或其他规模。此前准确率与协议遵循情况见
[云端合成题对照](cloud-model-comparison.md)。本轮新发现的八个 Qwen 型号和 1.5B
蒸馏型号没有执行相同的完整问答评测。

## 当前 Key＋端点上未能调用

错误仅对应本次 Key、端点、模型 ID 和验证时间。403/400 Access denied 或 404
不能单独证明模型全球下线，也不能推导为更换 Key 一定能解决。

| 规模 | 模型 ID | 实际响应 |
| --- | --- | --- |
| 110B | qwen1.5-110b-chat | 404 |
| 72B | qwen1.5-72b-chat | 404 |
| 70B | deepseek-r1-distill-llama-70b | 403 |
| 57B/A14B | qwen2-57b-a14b-instruct | 404 |
| 32B | deepseek-r1-distill-qwen-32b；qwen1.5-32b-chat | 均 404 |
| 14B | qwen2.5-14b-instruct | 403，前一轮当天实测 |
| 14B | qwen3-14b；qwen1.5-14b-chat | 均 404，前一轮当天实测 |
| 14B | deepseek-r1-distill-qwen-14b | 404 |
| 9B | qwen3.5-9b | 400，access_denied |
| 8B | qwen3-8b；deepseek-r1-distill-llama-8b | 均 404 |
| 7B | qwen2.5-7b-instruct | 403，前一轮当天实测 |
| 7B | qwen2-7b-instruct | 404，前一轮当天实测 |
| 7B | qwen1.5-7b-chat；qwen-7b-chat；deepseek-r1-distill-qwen-7b | 均 404 |
| 4B | qwen3-4b | 403 |
| 4B | qwen3.5-4b | 400，access_denied |
| 2B | qwen3.5-2b | 400，access_denied |
| 1.8B | qwen-1.8b-chat | 404 |
| 1.5B | qwen2.5-1.5b-instruct | 403 |
| 1.5B | qwen2-1.5b-instruct | 404 |
| 0.8B | qwen3.5-0.8b | 400，access_denied |
| 0.5B | qwen2.5-0.5b-instruct | 403 |
| 0.5B | qwen2-0.5b-instruct | 404 |

## 可复核证据

私有记录 `.cache/cloud-model-catalog-2026-10-19/new-probes.json` 保存 31 次新探测的
完整请求正文、原始响应、request ID、用量和状态；不保存 Authorization 头。
`catalog.json` 合并并标识了当天之前 7 个结果的出处；`summary.json` 保存汇总。
先前记录在 `.cache/cloud-model-probe`，未覆盖。

31 次新探测请求累计 14.563 秒。九个成功响应已知输入 193、输出 443 token；
22 次失败没有返回 usage，未当成零计费。密钥仅放在 0600 临时文件，完成核查后删除。
