"""Bond 关系系统的 AstrBot 原生 dashboard Web API。"""

from __future__ import annotations

from astrbot.api import logger
from dataclasses import asdict
from uuid import uuid4

from ..core.scope import GLOBAL_GROUP, account_id, scope_mode as validate_scope_mode
from ..core.settings import SettingsRevisionError

PLUGIN_NAME = "astrbot_plugin_bond"

try:  # AstrBot 4.28.0 的 FastAPI 面使用 Quart 兼容调用层
    from quart import g, jsonify, request
except ImportError:  # pragma: no cover
    g = None
    jsonify = None
    request = None


ERROR_UNAUTHORIZED = "未授权"
ERROR_INVALID = "请求参数无效"
ERROR_SETTINGS_REVISION = "设置已变更，请刷新后重试"
ERROR_BOT = "Bot 或数据空间不可用"
ERROR_OPERATION = "操作失败"
ERROR_UNDO = "记录不存在或不可撤销"
ERROR_BACKUP = "备份不存在或无效"


class _PageRequestError(Exception):
    pass


class _PageOperationError(Exception):
    pass


class PageApi:
    """Bond 面板 API；所有业务访问都从请求目标选择固定的 runtime bundle。"""

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._context = None

    @staticmethod
    async def _params() -> dict:
        """合并查询串与 JSON body，body 中的值优先。"""
        if request is None:
            return {}
        params = {k: v for k, v in request.args.items()}
        try:
            body = await request.get_json(silent=True)
            if isinstance(body, dict):
                params.update(body)
        except Exception:
            pass
        return params

    @staticmethod
    def _response(payload: dict, status: int = 200):
        if jsonify is None:  # pragma: no cover
            return payload, status
        response = jsonify(payload)
        if status != 200:
            return response, status
        return response

    @staticmethod
    def _error(message: str, status: int = 400):
        return PageApi._response({"success": False, "error": message}, status)

    @staticmethod
    def _username() -> str | None:
        """读取宿主认证中间件写入的 g.username，不读取客户端字段。"""
        if g is None:
            return None
        try:
            value = getattr(g, "username", None)
        except Exception:
            return None
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    @classmethod
    def _auth_error(cls):
        if cls._username() is None:
            return cls._error(ERROR_UNAUTHORIZED, 401)
        return None

    def _target(self, params: dict) -> tuple[str, str]:
        try:
            bot_id = account_id(params.get("bot_id"))
            mode = validate_scope_mode(params.get("scope_mode"))
        except (TypeError, ValueError):
            raise _PageRequestError from None
        try:
            known = self._runtime.known_bot_ids()
        except Exception as exc:
            raise _PageOperationError from exc
        if bot_id not in known:
            raise _PageRequestError
        return bot_id, mode

    async def _deps(self, params: dict):
        bot_id, mode = self._target(params)
        try:
            deps = await self._runtime.select(bot_id, mode)
        except Exception as exc:
            raise _PageOperationError from exc
        return bot_id, mode, deps

    @staticmethod
    def _optional_account(params: dict, key: str) -> str | None:
        value = params.get(key)
        if value is None or value == "":
            return None
        try:
            return account_id(value)
        except (TypeError, ValueError):
            raise _PageRequestError from None

    @staticmethod
    def _required_account(params: dict, key: str) -> str:
        value = PageApi._optional_account(params, key)
        if value is None:
            raise _PageRequestError
        return value

    @staticmethod
    def _limit(params: dict, key: str, default: int, maximum: int) -> int:
        value = params.get(key, default)
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise _PageRequestError from None
        return max(1, min(value, maximum))

    @staticmethod
    def _offset(params: dict) -> int:
        try:
            value = int(params.get("offset", 0))
        except (TypeError, ValueError):
            raise _PageRequestError from None
        return max(0, value)

    @staticmethod
    def _positive_int(value) -> int:
        if isinstance(value, bool) or isinstance(value, float):
            raise _PageRequestError
        if isinstance(value, str):
            if not value.isascii() or not value.isdigit() or value.startswith("0"):
                raise _PageRequestError
        elif not isinstance(value, int):
            raise _PageRequestError
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise _PageRequestError from None
        if number <= 0:
            raise _PageRequestError
        return number

    async def _invoke(self, callback):
        denied = self._auth_error()
        if denied is not None:
            return denied
        try:
            return await callback()
        except _PageRequestError:
            return self._error(ERROR_INVALID)
        except _PageOperationError:
            logger.debug("[八千代] 面板接口操作失败", exc_info=True)
            return self._error(ERROR_OPERATION, 500)
        except Exception:
            logger.debug("[八千代] 面板接口异常", exc_info=True)
            return self._error(ERROR_OPERATION, 500)

    def register(self, context) -> None:
        """注册原生插件 API；宿主负责把这些 handler 放入已认证 dashboard。"""
        self._context = context
        reg = getattr(context, "register_web_api", None)
        if reg is None or jsonify is None:
            return
        # 宿主只替换方法列表完全一致的路由；升级时 GET → GET/POST 会残留旧处理器。
        registered = getattr(context, "registered_web_apis", None)
        if isinstance(registered, list):
            registered[:] = [
                entry
                for entry in registered
                if not entry[0].startswith(f"/{PLUGIN_NAME}/")
            ]
        for endpoint, handler, methods, label in (
            ("settings", self.handle_settings, ["GET", "POST"], "Bond 详细设置"),
            ("relation", self.handle_relation, ["POST"], "Bond 修改三维关系"),
            ("profile", self.handle_profile, ["POST"], "Bond 修改印象与标签"),
            ("prompts", self.handle_prompts, ["GET", "POST"], "Bond 提示词设置"),
            ("models", self.handle_models, ["GET", "POST"], "Bond 评估模型设置"),
            ("diagnostics", self.handle_diagnostics, ["GET", "POST"], "Bond 关系诊断"),
        ):
            reg(f"/{PLUGIN_NAME}/{endpoint}", handler, methods, label)
        reg(
            f"/{PLUGIN_NAME}/assessment_stats",
            self.handle_assessment_stats,
            ["GET"],
            "Bond 评估调用统计",
        )
        reg(f"/{PLUGIN_NAME}/logs", self.handle_logs, ["GET"], "Bond 关系变动记录")
        reg(
            f"/{PLUGIN_NAME}/groups",
            self.handle_groups,
            ["GET"],
            "Bond 有记录的群列表",
        )
        reg(f"/{PLUGIN_NAME}/users", self.handle_users, ["GET"], "Bond 当前关系总览")
        reg(
            f"/{PLUGIN_NAME}/undo",
            self.handle_undo,
            ["POST"],
            "Bond 撤销/预览一次变动",
        )
        reg(
            f"/{PLUGIN_NAME}/member",
            self.handle_member,
            ["GET", "POST"],
            "Bond 成员详情（印象/标签/近期评审）",
        )
        reg(
            f"/{PLUGIN_NAME}/refresh_impression",
            self.handle_refresh_impression,
            ["POST"],
            "Bond 立即刷新成员印象",
        )
        reg(
            f"/{PLUGIN_NAME}/bots",
            self.handle_bots,
            ["GET"],
            "Bond 已知 Bot 与当前空间",
        )
        reg(
            f"/{PLUGIN_NAME}/mode", self.handle_mode, ["POST"], "Bond 切换 Bot 数据空间"
        )
        reg(
            f"/{PLUGIN_NAME}/backup",
            self.handle_backup,
            ["POST"],
            "Bond 备份当前数据空间",
        )
        reg(
            f"/{PLUGIN_NAME}/restore",
            self.handle_restore,
            ["POST"],
            "Bond 恢复当前数据空间",
        )

    async def handle_bots(self):
        """返回当前连接及已有本地记录的 Bot；这是已认证的元数据端点，不选定数据空间。"""

        async def run():
            try:
                bots = await self._runtime.describe()
            except Exception as exc:
                raise _PageOperationError from exc
            return self._response({"success": True, "bots": bots})

        return await self._invoke(run)

    async def handle_mode(self):
        async def run():
            params = await self._params()
            bot_id, mode = self._target(params)
            try:
                await self._runtime.switch_mode(bot_id, mode)
            except ValueError:
                raise _PageRequestError from None
            except Exception as exc:
                raise _PageOperationError from exc
            return self._response(
                {"success": True, "bot_id": bot_id, "scope_mode": mode}
            )

        return await self._invoke(run)

    async def handle_logs(self):
        async def run():
            params = await self._params()
            _bot_id, _mode, deps = await self._deps(params)
            group_id = self._optional_account(params, "group_id")
            fuzzy = str(params.get("fuzzy") or "").strip() == "1"
            user_id = params.get("user_id") or None
            if fuzzy and user_id is not None:
                if not (
                    isinstance(user_id, str)
                    and user_id.isascii()
                    and user_id.isdigit()
                    and len(user_id) <= 32
                ):
                    raise _PageRequestError
            else:
                user_id = self._optional_account(params, "user_id")
            dimension = params.get("dimension") or None
            source = params.get("source") or None
            if dimension not in (
                None,
                "familiarity",
                "affinity",
                "trust",
            ) or source not in (None, "judge"):
                raise _PageRequestError
            logs = await deps.favor.query_logs(
                group_id,
                user_id,
                self._limit(params, "limit", 300, 1000),
                self._offset(params),
                fuzzy=fuzzy,
                dimension=dimension,
                source=source,
            )
            return self._response({"success": True, "logs": logs, "count": len(logs)})

        return await self._invoke(run)

    async def handle_groups(self):
        async def run():
            _bot_id, _mode, deps = await self._deps(await self._params())
            groups = await deps.favor.distinct_groups()
            return self._response({"success": True, "groups": groups})

        return await self._invoke(run)

    async def handle_users(self):
        async def run():
            params = await self._params()
            _bot_id, _mode, deps = await self._deps(params)
            group_id = self._optional_account(params, "group_id")
            page = self._positive_int(params.get("page", 1))
            page_size = self._positive_int(params.get("page_size", 50))
            search = params.get("search", "")
            include_zero = params.get("include_zero", "0")
            if (
                page_size not in (30, 50, 100, 200)
                or not isinstance(search, str)
                or len(search) > 32
                or (search.strip() and not search.strip().isascii())
                or (search.strip() and not search.strip().isdigit())
            ):
                raise _PageRequestError
            if type(include_zero) is bool:
                expanded = include_zero
            elif type(include_zero) is str and include_zero in {"0", "1"}:
                expanded = include_zero == "1"
            else:
                raise _PageRequestError
            result = await deps.favor.standings_page(
                group_id,
                page=page,
                page_size=page_size,
                search=search,
                include_zero=expanded,
            )
            return self._response({"success": True, **result})

        return await self._invoke(run)

    @staticmethod
    def _relation_group(params, mode):
        if mode == "global" and not params.get("group_id"):
            return GLOBAL_GROUP
        return PageApi._required_account(params, "group_id")

    async def handle_member(self):
        async def run():
            params = await self._params()
            _bot_id, _mode, deps = await self._deps(params)
            group_id = self._relation_group(params, _mode)
            user_id = self._required_account(params, "user_id")
            if request is not None and request.method == "POST":
                await self._runtime.refresh_names(
                    _bot_id,
                    _mode,
                    "" if group_id == GLOBAL_GROUP else group_id,
                    user_id,
                )
            rec = await deps.favor.get(group_id, user_id)
            logs = (
                await deps.favor.query_logs(group_id, user_id, limit=30)
                if group_id != GLOBAL_GROUP
                else []
            )
            judged = [row for row in logs if row.get("source") == "judge"][:10]
            return self._response(
                {
                    "success": True,
                    "member": {
                        "group_id": "" if group_id == GLOBAL_GROUP else group_id,
                        "user_id": user_id,
                        "nickname": await deps.favor.member_name(
                            None if group_id == GLOBAL_GROUP else group_id, user_id
                        ),
                        "favor": rec.favor,
                        "affinity": rec.affinity,
                        "familiarity": rec.familiarity,
                        "trust": rec.trust,
                        "temporary": await deps.favor.temporary_state(
                            group_id, user_id
                        ),
                        "formal_relation": await deps.favor.formal_relation(
                            group_id, user_id
                        ),
                        "temporary_audit": await deps.favor.temporary_audit(
                            group_id, user_id
                        ),
                        "relation_title": await deps.favor.relation_title(
                            group_id, user_id
                        ),
                        "level": deps.favor.level_of(rec.favor).name,
                        "relationship": (
                            deps.favor.relationship_label(rec.relationship)
                            if rec.relationship
                            else ""
                        ),
                        "is_master": deps.favor.is_master(user_id),
                        "impression": rec.impression or "",
                        "tags": rec.parsed_tags(),
                        "impression_at": rec.impression_at,
                        "recent_judges": [
                            {
                                "ts": row.get("ts"),
                                "delta": row.get("delta"),
                                "reason": row.get("reason") or "",
                                "message": row.get("message") or "",
                            }
                            for row in judged
                        ],
                    },
                }
            )

        return await self._invoke(run)

    async def handle_refresh_impression(self):
        async def run():
            params = await self._params()
            _bot_id, _mode, deps = await self._deps(params)
            group_id = self._required_account(params, "group_id")
            user_id = self._required_account(params, "user_id")
            if deps.impressions is None:
                return self._error("印象功能未启用")
            ok, message = await deps.impressions.refresh_now(group_id, user_id)
            return self._response({"success": ok, "message": message})

        return await self._invoke(run)

    async def handle_undo(self):
        async def run():
            params = await self._params()
            _bot_id, _mode, deps = await self._deps(params)
            log_id = self._positive_int(params.get("id"))
            dry = str(params.get("dry") or "").strip() == "1"
            try:
                if dry:
                    info = await deps.favor.undo_preview(log_id)
                    return self._response({"success": True, "preview": info})
                rec = await deps.favor.undo_log(log_id, actor=self._username())
            except (TypeError, ValueError):
                return self._error(ERROR_UNDO)
            return self._response({"success": True, "favor": rec.favor})

        return await self._invoke(run)

    async def handle_backup(self):
        async def run():
            params = await self._params()
            bot_id, mode = self._target(params)
            try:
                backup_path = await self._runtime.storage.backup(bot_id, mode)
                backup_name = backup_path.name
            except Exception as exc:
                raise _PageOperationError from exc
            return self._response({"success": True, "backup_name": backup_name})

        return await self._invoke(run)

    async def handle_restore(self):
        async def run():
            params = await self._params()
            bot_id, mode = self._target(params)
            backup_name = params.get("backup_name")
            if (
                not isinstance(backup_name, str)
                or not backup_name.isascii()
                or not backup_name
                or len(backup_name) > 128
            ):
                raise _PageRequestError
            try:
                await self._runtime.restore(bot_id, mode, backup_name)
            except (FileNotFoundError, ValueError):
                return self._error(ERROR_BACKUP)
            except Exception as exc:
                raise _PageOperationError from exc
            return self._response(
                {"success": True, "bot_id": bot_id, "scope_mode": mode}
            )

        return await self._invoke(run)

    async def handle_assessment_stats(self):
        async def run():
            _bot_id, _mode, deps = await self._deps(await self._params())
            return self._response(
                {"success": True, "stats": await deps.assessment.stats()}
            )

        return await self._invoke(run)

    async def handle_relation(self):
        async def run():
            params = await self._params()
            _, mode, deps = await self._deps(params)
            group = self._relation_group(params, mode)
            user = self._required_account(params, "user_id")
            reason = params.get("reason", "管理员调整")
            if not isinstance(reason, str) or len(reason) > 120:
                raise _PageRequestError
            try:
                action = params.get("action", "permanent")
                if action in ("temporary", "cancel_temporary"):
                    values = (
                        {
                            key: params.get(key)
                            for key in ("familiarity", "affinity", "trust")
                        }
                        if action == "temporary"
                        else None
                    )
                    await deps.favor.set_temporary(
                        group,
                        user,
                        values,
                        params.get("duration_sec"),
                        self._username(),
                        reason,
                    )
                    return self._response({"success": True})
                if action != "permanent":
                    raise ValueError("操作无效")
                result = await deps.favor.set_relation(
                    group,
                    user,
                    familiarity=params.get("familiarity"),
                    affinity=params.get("affinity"),
                    trust=params.get("trust"),
                    event_id="admin:" + uuid4().hex,
                    reason=f"Web 管理员 {self._username()}: {reason}",
                )
            except (ValueError, TypeError):
                raise _PageRequestError from None
            return self._response({"success": True, "relation": result["after"]})

        return await self._invoke(run)

    async def handle_profile(self):
        async def run():
            params = await self._params()
            _, _, deps = await self._deps(params)
            group = self._required_account(params, "group_id")
            user = self._required_account(params, "user_id")
            try:
                await deps.favor.set_profile(
                    group,
                    user,
                    impression=params.get("impression"),
                    tags=params.get("tags"),
                    actor=self._username(),
                )
            except (ValueError, TypeError):
                raise _PageRequestError from None
            return self._response({"success": True})

        return await self._invoke(run)

    async def handle_models(self):
        async def run():
            params = await self._params()
            _, _, deps = await self._deps(params)
            if request.method == "POST":
                try:
                    revision = await self._runtime.save_assessment_settings(
                        params.get("config"),
                        params.get("max_concurrent_assessments"),
                        params.get("revision"),
                    )
                except SettingsRevisionError:
                    return self._error(ERROR_SETTINGS_REVISION, 409)
                except (ValueError, TypeError):
                    raise _PageRequestError from None
                return self._response(
                    {"success": True, "restart_required": True, "revision": revision}
                )
            configured, limit = self._runtime.assessment_settings()
            return self._response(
                {
                    "success": True,
                    "configured": configured,
                    "active": asdict(deps.assessment.config),
                    "max_concurrent_assessments": limit,
                    "active_max_concurrent_assessments": deps.assessment.capacity.limit,
                    "revision": self._runtime.detailed_settings()["revision"],
                }
            )

        return await self._invoke(run)

    async def handle_prompts(self):
        async def run():
            params = await self._params()
            bot, _, deps = await self._deps(params)
            kind = params.get("kind")
            if not isinstance(kind, str) or kind not in {"assessment", "behavior"}:
                raise _PageRequestError
            level = params.get("level", "global")
            if not isinstance(level, str) or level not in {"global", "group"}:
                raise _PageRequestError
            group = (
                self._required_account(params, "group_id") if level == "group" else None
            )
            scope_bot = bot if group else None
            if deps.prompts.load_error:
                return self._error(deps.prompts.load_error, 503)
            try:
                if request.method == "POST":
                    action = params.get("action")
                    if action == "reset":
                        deps.prompts.reset(kind, scope_bot, group)
                    elif action == "save":
                        deps.prompts.set_template(
                            kind,
                            scope_bot,
                            group,
                            params.get("mode"),
                            params.get("template", ""),
                        )
                    elif action == "preview":
                        preview = deps.prompts.preview(
                            kind,
                            scope_bot,
                            group,
                            mode=params.get("mode"),
                            template=params.get("template", ""),
                        )
                        return self._response({"success": True, "preview": preview})
                    else:
                        raise _PageRequestError
                settings = deps.prompts.get_settings(kind, scope_bot, group)
                resolved = deps.prompts.resolve(kind, scope_bot, group)
                return self._response(
                    {"success": True, "settings": settings, "resolved": resolved}
                )
            except (ValueError, TypeError):
                raise _PageRequestError from None

        return await self._invoke(run)

    async def handle_settings(self):
        async def run():
            params = await self._params()
            providers = []
            if self._context is not None:
                for provider in self._context.get_all_providers():
                    meta = provider.meta()
                    providers.append({"id": meta.id, "model": meta.model})
            settings = None
            if request.method == "POST":
                try:
                    settings = await self._runtime.save_detailed_settings(
                        params.get("values"), params.get("revision")
                    )
                except SettingsRevisionError:
                    return self._error(ERROR_SETTINGS_REVISION, 409)
                except (ValueError, TypeError, KeyError):
                    return self._error("设置无效或已被修改，请刷新页面并检查输入")
            return self._response(
                {
                    "success": True,
                    **(settings or self._runtime.detailed_settings()),
                    "providers": providers,
                    "restart_required": request.method == "POST",
                }
            )

        return await self._invoke(run)

    async def handle_diagnostics(self):
        async def run():
            params = await self._params()
            bot_id, mode = self._target(params)
            recorder = self._runtime.diagnostics
            if recorder is None:
                return self._error("诊断记录不可用")
            action = params.get("action", "status")
            if request.method == "POST" and action == "clear":
                cleared = await recorder.clear(bot_id, mode)
                if not cleared["clear_succeeded"]:
                    raise _PageOperationError
            elif request.method == "GET" and action == "export":
                return self._response(
                    {
                        "success": True,
                        "diagnostics": await recorder.export(bot_id, mode),
                    }
                )
            elif request.method != "GET" or action != "status":
                raise _PageRequestError
            return self._response(
                {"success": True, "diagnostics": recorder.stats(bot_id, mode)}
            )

        return await self._invoke(run)
