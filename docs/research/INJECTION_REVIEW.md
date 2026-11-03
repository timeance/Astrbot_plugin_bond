# 关系资料放在哪里

核验日期：2026-11-03。Bond 基于 0.3.8，框架固定为 AstrBot 4.28.0。

## 结论

本版保留向 `req.system_prompt` 追加关系资料的实现。后续值得对照的方案是：固定的使用边界保留在 system，成员关系、印象和近期事件放入临时 user 内容片段。暂不整体迁移，也不增加注入模式开关。

这能让固定提示词更稳定，但不能据此承诺更高缓存命中率、更低费用或更贴合人格。不同模型、Provider 和运行器的处理存在差异，需要实际验证。

## 框架核验

| 项目 | 当前追加 system | 临时 user 内容片段 |
| --- | --- | --- |
| 资料位置 | 宿主人格后的同一条 system 消息 | 当前用户正文后的内容片段，仍属同一条 user 消息 |
| 普通历史保存 | 框架跳过首条 system，关系原文不会因此写入普通历史 | 标记为临时的片段会被历史序列化过滤 |
| 重试、工具续轮 | 当前运行上下文继续使用 | 临时片段继续可见，不会在一次模型调用后立即消失 |
| 摘要压缩 | 不能凭注入位置保证派生内容不会保留 | 摘要模型能看到临时内容，生成的普通摘要可能保留它 |
| 人格与披露规则 | 维持已验证的追加行为 | 应把固定边界留在 system，不能把整块规则直接降到 user |

正确写法是 `TextPart(text=...).mark_as_temp()`，再放入 `req.extra_user_content_parts`。4.28.0 没有 `make_as_temp()`；漏掉临时标记的 user 片段会进入普通历史。

真实框架的合成探针通过16项：当前消息装配、临时标记、OpenAI Chat/Responses/Anthropic/Gemini 本地序列化、空响应重试、工具续轮、实际历史保存及摘要传播。摘要探针故意让模拟模型复制标记，证明存在这条数据路径，不代表真实模型必然复制。工具续轮使用合成工具消息，没有执行外部工具。

OpenAI Responses 在本次默认运行器路径中使用 `input` 内的 system 消息，不能笼统称为 `instructions`。Dify 运行器读取 `system_prompt` 变量而未消费这个 extra 字段；Coze 也有自身的会话条件。因此不能对所有运行器统一替换。

## 缓存判断与后续条件

保持公共前缀稳定、把变化内容放在后面，有利于构造可复用的输入；是否命中仍取决于模型、缓存边界、长度和保留策略。OpenAI 官方文档也要求对比缓存 Token、延迟与总成本，不能只看接口名称。

若后续实施，先用同一人格、相同消息和关系状态对照两种方式，检查自然表达、公开称谓、私有资料披露、摘要残留及重试。再接真实 Provider 统计缓存 Token 与费用。本轮未连接真实 Provider、QQ 或浏览器，未测语义效果和缓存收益。

## 源码与资料

- Bond：`services/inject_service.py::build_relation_block` 与 `inject`，`api/listeners.py` 的 LLM 请求钩子。
- [AstrBot 4.28.0 请求装配](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.0/astrbot/core/provider/entities.py#L173)。
- [临时片段标记与序列化](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.0/astrbot/core/agent/message.py#L68)。
- [历史保存](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.0/astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py#L473)。
- [摘要输入与结果](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.0/astrbot/core/agent/context/compressor.py#L240)。
- [OpenAI 缓存说明](https://developers.openai.com/api/docs/guides/prompt-caching)。
