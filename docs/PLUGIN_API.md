# 插件间调用

这些接口只供同一 AstrBot 进程中已安装的插件使用，不是 HTTP 接口、聊天命令或模型工具。调用方仍须遵守自己的访问权限，不要把接口直接转发给聊天用户。

```python
star = context.get_registered_star("astrbot_plugin_bond")
if star and star.activated:
    api = star.star_cls.api
    profile = await api.get_profile(bot_id, mode, group_id, user_id)
    ranking = await api.get_ranking(bot_id, mode, group_id, limit=5)
    submit = getattr(api, "submit_assessment_event_v1", None)
    if submit:
        status = await submit(event)
```

- 查询必须指定真实 Bot 账号和关系空间（`per_group` 或 `global`）。返回公开关系称谓，不返回分值、印象或聊天记录。
- `submit_assessment_event_v1(event)` 接收当前 AstrBot 平台事件，不接收自填的成员、文本、关系类别或分值。首版只接受 OneBot 群聊里明确提及或回复机器人的消息。普通插件不能提交脱离平台消息的自定义事实。
- 管理员须在「基础设置」开启「允许其他插件提交群聊互动评估」和「自动评估」，保存并重载。调用会等待 Bond 处理；它不是持久待办队列。与原生评估共用消息标识、并发限制、模型和额度。`accepted` 表示评估已产生变动；`disabled`、`filtered`、`busy`、`duplicate_or_cooldown`、`abstained` 等均不能视为已改分。
- 反注入联动独立开启，不使用这个通用评估入口。Bond 只从宿主注册的 AntiPromptInjector 领取本轮防护事实，其他调用方传入自写的「已确认」结论不会改分。此校验用于区分正常调用路径，不提供同进程恶意插件隔离。
