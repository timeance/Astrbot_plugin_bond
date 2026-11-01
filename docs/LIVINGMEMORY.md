# LivingMemory 可选适配

## 调查结论

调查日期：2026-11-01。

公开仓库 `lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory` 的 `master` 当前提交为 `a1e9c20a8c5fab93d6143f51be26fb1bbf8143ec`，提交信息为 `chore(release): 2.8.2`。本地 PR-0 记录的基线与公开仓库一致。

核对了以下路径：

- `metadata.yaml`：插件名 `astrbot_plugin_livingmemory`，版本 `2.8.2`。
- `main.py`：入口为继承 `Star` 的 `LivingMemoryPlugin`，依赖元数据自动发现；没有独立的 `self.api` 服务门面。
- `main.py::_register_agent_tools_if_needed`：通过 `context.add_llm_tools(...)` 条件注册模型工具。
- `core/tools/memory_search_tool.py`：注册 `recall_long_term_memory`，从 `ContextWrapper` 取得当前事件后调用内部 `memory_engine.search_memories(...)`。
- `core/tools/memory_memorize_tool.py`：注册 `memorize_long_term_memory`，从当前事件解析作用域后调用内部 `memory_engine.add_memory(...)`。
- `core/tools/memory_read_tool.py`：注册可选的 `read_memory_evidence`，读取仍经内部 engine 和当前工具访问策略。
- `core/tools/memory_forget_tool.py`：注册 `forget_long_term_memory`，带管理员检查和软删除逻辑，仍是模型工具内部实现。
- `core/memory_scope.py`：作用域可为当前 `unified_msg_origin`、`livingmemory:user:<platform>:<identity>` 或固定 `livingmemory:global`。
- `core/passive_group_capture.py`：`get_active_plugin()` 是插件内部弱引用，不能作为跨插件公开 API。

这些工具的访问上下文包含 session、平台、发送者和群组信息，但没有能独立证明 Bond 的真实 Bot ID 与群来源的公开服务契约。`session`、`user` 和 `global` 模式还可能跨会话或跨群共享。直接调用 `initializer.memory_engine`、任何数据库、工具对象或插件内部弱引用都会绕过上游策略，也不能证明来源隔离，因此本适配器不调用它们。

## 当前实现

`LivingMemoryService` 保持 Bond 的 `standalone` 默认模式。它不导入 LivingMemory，不添加运行时依赖，不访问 LivingMemory 数据库，也不保存插件实例。

构造：

```python
service = LivingMemoryService(context, config)
```

配置使用三个独立开关，默认均为关闭：

```python
{
    "livingmemory": {
        "enabled": False,
        "read_enabled": False,
        "write_enabled": False,
    }
}
```

父集成可调用：

```python
state = service.status()
read_result = await service.recall(
    query="用户偏好",
    bot_id=bot_id,
    group_id=group_id,
    user_id=user_id,
)
write_result = await service.memorize(
    memory="用户偏好简洁回答",
    bot_id=bot_id,
    group_id=group_id,
    user_id=user_id,
    source_id="bond",
    event_id=event_id,
)
```

配置只接受嵌套字典中的三个布尔值。缺少键按 `false` 处理；其它类型返回 `invalid_config`，管理入口可调用 `LivingMemoryService.validate_config(config)` 取得 `ValueError`。`status()`、`recall()` 和 `memorize()` 每次调用都会重新读取配置；在 `enabled=false` 时直接返回 `standalone`，不调用 `context`。启用后每次调用都通过 `context.get_registered_star("astrbot_plugin_livingmemory")` 重新发现真实 `StarMetadata`，检查 `activated` 和 `star_cls`，不缓存旧对象。状态同时给出 `read.enabled`、`read.available`、`read.reason` 和对应的 `write` 字段，父集成应以 `available` 为准。

当前活跃插件的状态是 `unsupported_scope`，`reason_code` 为 `public_api_missing`。读取返回空 `items`，写入返回 `written=false`。这表示当前公开接口不能安全适配，不表示读取或写入成功。缺失、未激活、注册异常、实例不可用和动态关闭都在本地降级，不影响 Bond 自有 SQLite 和正常回复。

## 后续启用条件

只有上游提供稳定公开服务 API，并同时返回或验证以下边界，才可以增加真实读取：真实 Bot ID、平台、群或私聊作用域、用户身份、超时行为和来源字段。读取仍需限长、超时和去重；`global` 关系不能授权召回其它群的原始记忆。

写入还需要稳定的 `source_id` 与 `event_id` 持久幂等契约。外部记忆文本只能作为待校验上下文，不能直接修改 Bond 的 familiarity、affinity、trust、印象或事件状态。满足这些条件前，父集成应保持 `read.available=false` 和 `write.available=false`。
