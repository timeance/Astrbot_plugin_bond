# LivingMemory 集成移除说明

核验日期：2026-11-02。调查源码固定在以下提交，结论不代表未来版本永远不可接入。

LivingMemory 集成已按维护者要求移除。Bond 不再提供相关设置、页面、探测服务或 API。升级时旧的 `livingmemory` 设置由宿主配置校验清理；不修改 Bond 数据库，也不修改或删除 LivingMemory 数据。

## 已有调用路径

LivingMemory 2.8.4，提交 `bfb8b1321daf93b628bf819c3a28328d940cb063`：插件注册模型工具，检索引擎通过 `initializer.memory_engine` 暴露在插件实例上。不能将其描述成“完全没有接口”。

[group_chat_plus 的 memory_injector.py](https://github.com/Him666233/astrbot_plugin_group_chat_plus/blob/aa97e0e35a18733e73db753238dd2a3021446e42/utils/memory_injector.py) 通过 AstrBot 的插件注册表取得 LivingMemory 实例，访问 `initializer.memory_engine.search_memories()`，传入 `event.unified_msg_origin` 和人格 ID。该项目确实有调用实现；本次没有运行该项目，不声称其所有版本和场景均验证通过。

## Bond 尚缺少的保证

AstrBot 4.28.0 的消息来源由适配器标识、消息类型和会话 ID 组成，不保证包含真实 Bot 账号。一个 OneBot 适配器连接多个账号时，不能仅凭会话和人格证明记忆属于哪个 Bot。

LivingMemory 的 `core/memory_scope.py` 支持会话、用户和全局模式；用户／全局范围可能共享会话内容。真实 Bot 账号来源必须同时在写入及召回结果中可验证，不能只给搜索函数传入 Bot 自造的字符串，或把人格 ID 当作 Bot 身份。

这条调用路径尚不能满足 Bond 的多 Bot 隔离验收。因此移除集成，继续使用本地关系、印象和历史记录。未来如重新接入，需另行确认范围和权限；本次不保留占位开关或空实现。
