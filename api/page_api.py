"""Bond 关系系统的 AstrBot 原生 dashboard Web API。"""

from __future__ import annotations

from astrbot.api import logger

from ..core.scope import account_id, scope_mode as validate_scope_mode

PLUGIN_NAME = "astrbot_plugin_bond"

try:  # AstrBot 4.28.0 的 FastAPI 面使用 Quart 兼容调用层
    from quart import g, jsonify, request
except ImportError:  # pragma: no cover
    g = None
    jsonify = None
    request = None


ERROR_UNAUTHORIZED = "未授权"
ERROR_INVALID = "请求参数无效"
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
            known = self._runtime.storage.known_bot_ids()
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
            logger.debug("[心弦] 面板接口操作失败", exc_info=True)
            return self._error(ERROR_OPERATION, 500)
        except Exception:
            logger.debug("[心弦] 面板接口异常", exc_info=True)
            return self._error(ERROR_OPERATION, 500)

    def register(self, context) -> None:
        """注册原生插件 API；宿主负责把这些 handler 放入已认证 dashboard。"""
        reg = getattr(context, "register_web_api", None)
        if reg is None or jsonify is None:
            return
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
            ["GET"],
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
        """返回已产生可信事件的 Bot；这是已认证的元数据端点，不选定数据空间。"""

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
            logs = await deps.favor.query_logs(
                group_id,
                user_id,
                self._limit(params, "limit", 300, 1000),
                self._offset(params),
                fuzzy=fuzzy,
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
            users = await deps.favor.standings(
                group_id,
                self._limit(params, "limit", 500, 2000),
            )
            return self._response(
                {"success": True, "users": users, "count": len(users)}
            )

        return await self._invoke(run)

    async def handle_member(self):
        async def run():
            params = await self._params()
            _bot_id, _mode, deps = await self._deps(params)
            group_id = self._required_account(params, "group_id")
            user_id = self._required_account(params, "user_id")
            rec = await deps.favor.get(group_id, user_id)
            logs = await deps.favor.query_logs(group_id, user_id, limit=30)
            judged = [row for row in logs if row.get("source") == "judge"][:10]
            return self._response(
                {
                    "success": True,
                    "member": {
                        "group_id": group_id,
                        "user_id": user_id,
                        "nickname": rec.nickname or "",
                        "favor": rec.favor,
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
                rec = await deps.favor.undo_log(log_id)
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
                await self._runtime.storage.restore(bot_id, mode, backup_name)
            except (FileNotFoundError, ValueError):
                return self._error(ERROR_BACKUP)
            except Exception as exc:
                raise _PageOperationError from exc
            return self._response(
                {"success": True, "bot_id": bot_id, "scope_mode": mode}
            )

        return await self._invoke(run)
