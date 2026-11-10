"""用完全合成的数据预览原生 dashboard，只绑定本机且拒绝所有写请求。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "dashboard_demo.json"
DASHBOARD = ROOT / "pages" / "dashboard"
AXES = ("familiarity", "affinity", "trust")
READ_ONLY = "仅演示，不影响真实环境。此服务器只允许读取合成数据，写请求已拒绝。"
BRIDGE = """(() => {
  const context = Object.freeze({
    isDark: new URLSearchParams(location.search).get('theme') === 'dark',
    pluginName: 'astrbot_plugin_bond', demo: true, readOnly: true
  });
  window.AstrBotPluginPage = Object.freeze({
    getContext: () => context,
    ready: async () => context,
    onContext: callback => { callback(context); return () => {}; },
    apiGet: async (endpoint, params = {}) => {
      const query = new URLSearchParams(params);
      const response = await fetch('/api/' + encodeURIComponent(endpoint) + '?' + query,
        {credentials: 'omit', cache: 'no-store'});
      return response.json();
    },
    apiPost: async endpoint => {
      const response = await fetch('/api/' + encodeURIComponent(endpoint),
        {method: 'POST', credentials: 'omit'});
      return response.json();
    }
  });
})();
"""
BANNER = """<aside role="note" style="padding:10px 20px;background:#fff2b6;
color:#462d00;border-bottom:1px solid #b68a28;font:600 14px/1.6 sans-serif;
text-align:center">演示数据 · Bot、群、成员、印象与流水全部合成 · 只读预览</aside>"""


class PreviewServer(ThreadingHTTPServer):
    """仅装载静态页面与指定合成 fixture，不装配插件或数据库。"""

    def __init__(self, port: int, vue_js: Path | None = None):
        self.data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        if self.data.get("synthetic") is not True:
            raise ValueError("演示 fixture 必须明确标记 synthetic=true")
        self.vue_js = vue_js.resolve(strict=True) if vue_js else None
        now = time.time()
        offset = now - self.data["snapshot_at"]

        def shift(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"ts", "expires_at", "created_at", "impression_at"}:
                        if isinstance(item, (float, int)) and item > 0:
                            value[key] = item + offset
                    else:
                        shift(item)
            elif isinstance(value, list):
                for item in value:
                    shift(item)

        shift(self.data)
        self.data["snapshot_at"] = now
        self.call_day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        self.relation_zone = timezone(timedelta(hours=8))
        self.relation_day = (
            datetime.fromtimestamp(now, self.relation_zone).date().isoformat()
        )
        for row in self.data["assessment_stats"]["calls"]:
            row["day"] = self.call_day
        super().__init__(("localhost", port), PreviewHandler)

    def read_api(self, endpoint: str, params: dict) -> dict:
        if endpoint in {"bots", "settings"}:
            return (
                {endpoint: deepcopy(self.data[endpoint])}
                if endpoint == "bots"
                else deepcopy(self.data[endpoint])
            )
        bot = self.data["bots"][0]
        if (
            params.get("bot_id") != bot["bot_id"]
            or params.get("scope_mode") != bot["scope_mode"]
        ):
            raise ValueError("请选择演示 Bot 的按群空间")
        group = params.get("group_id", "")
        if group and group not in {row["group_id"] for row in self.data["groups"]}:
            raise ValueError("未知演示群")
        users = [
            row for row in self.data["users"] if not group or row["group_id"] == group
        ]
        if endpoint == "groups":
            return {"groups": deepcopy(self.data["groups"])}
        if endpoint == "users":
            search = params.get("search", "").strip()
            if search and (
                not search.isascii() or not search.isdigit() or len(search) > 32
            ):
                raise ValueError("成员搜索须为演示 QQ 号")
            page_size = int(params.get("page_size", "50"))
            page = int(params.get("page", "1"))
            expanded = params.get("include_zero", "0")
            if (
                page_size not in (30, 50, 100, 200)
                or page < 1
                or expanded not in {"0", "1"}
            ):
                raise ValueError("分页参数无效")
            matched = [row for row in users if search in row["user_id"]]
            zero_count = sum(all(row[axis] == 0 for axis in AXES) for row in matched)
            collapsed = zero_count if expanded == "0" and not search else 0
            rows = [
                row
                for row in matched
                if not collapsed or any(row[axis] != 0 for axis in AXES)
            ]
            rows.sort(
                key=lambda row: (-row["affinity"], row["group_id"], row["user_id"])
            )
            pages = max(1, (len(rows) + page_size - 1) // page_size)
            page = min(page, pages)
            visible = deepcopy(rows[(page - 1) * page_size : page * page_size])
            return dict(
                users=visible,
                count=len(visible),
                total=len(rows),
                matched_count=len(matched),
                zero_count=zero_count,
                collapsed_count=collapsed,
                page=page,
                page_size=page_size,
                pages=pages,
            )
        if endpoint == "member":
            member = next(
                (
                    row
                    for row in users
                    if row["user_id"] == params.get("user_id") and group
                ),
                None,
            )
            if member is None:
                raise ValueError("演示成员不存在，请选择群与成员")
            result = deepcopy(member)
            result["recent_judges"] = [
                {key: row[key] for key in ("ts", "delta", "reason", "message")}
                for row in self.data["logs"]
                if row["group_id"] == group
                and row["user_id"] == member["user_id"]
                and row["source"] == "judge"
            ][:10]
            return {"member": result}
        if endpoint == "logs":
            dimension = params.get("dimension")
            source = params.get("source")
            if dimension not in (None, *AXES) or source not in (None, "judge"):
                raise ValueError("流水筛选参数无效")
            user = params.get("user_id", "")
            limit = min(1000, max(1, int(params.get("limit", "300"))))
            rows = [
                row
                for row in self.data["logs"]
                if (not group or row["group_id"] == group)
                and (
                    not user
                    or (
                        user in row["user_id"]
                        if params.get("fuzzy") == "1"
                        else user == row["user_id"]
                    )
                )
                and (not source or row["source"] == source)
                and (not dimension or row["relation_delta"][dimension] != 0)
            ]
            return {"logs": deepcopy(rows[:limit]), "count": len(rows[:limit])}
        if endpoint == "today_summary":
            calls = self.data["assessment_stats"]["calls"]
            changed = {
                row["user_id"]
                for row in self.data["logs"]
                if any(row["relation_delta"].values())
                and datetime.fromtimestamp(row["ts"], self.relation_zone)
                .date()
                .isoformat()
                == self.relation_day
            }
            return {
                "summary": dict(
                    initial={
                        "used": sum(
                            row["count"] for row in calls if row["tier"] == "small"
                        ),
                        "limit": 300,
                    },
                    review={
                        "used": sum(
                            row["count"] for row in calls if row["tier"] == "large"
                        ),
                        "limit": 100,
                    },
                    changed_members=len(changed),
                    call_day=self.call_day,
                    relation_day=self.relation_day,
                    relation_timezone="Asia/Hong_Kong",
                    call_reset_at=datetime.fromtimestamp(
                        self.data["snapshot_at"], timezone.utc
                    )
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                    .timestamp()
                    + 86400,
                    relation_reset_at=datetime.fromtimestamp(
                        self.data["snapshot_at"], self.relation_zone
                    )
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                    .timestamp()
                    + 86400,
                )
            }
        if endpoint == "assessment_stats":
            return {"stats": deepcopy(self.data["assessment_stats"])}
        if endpoint == "models":
            return deepcopy(self.data["models"])
        if endpoint == "prompts":
            kind = params.get("kind")
            if kind not in {"behavior", "assessment"} or params.get(
                "level", "global"
            ) not in {"global", "group"}:
                raise ValueError("提示词参数无效")
            return {
                "settings": {
                    "global": {"mode": "default", "template": ""},
                    "groups": {},
                },
                "resolved": self.data["prompts"][kind],
            }
        if endpoint == "diagnostics":
            action = params.get("action", "status")
            if action == "export":
                return {
                    "diagnostics": {"synthetic": True, "records": []},
                    "status": deepcopy(self.data["diagnostics"]),
                }
            if action != "status":
                raise ValueError(READ_ONLY)
            return {"diagnostics": deepcopy(self.data["diagnostics"])}
        raise KeyError(endpoint)


class PreviewHandler(BaseHTTPRequestHandler):
    server: PreviewServer

    def log_message(self, format, *args):
        """控制台只输出启动入口。"""

    def send_content(self, content: bytes, content_type: str, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == HTTPStatus.METHOD_NOT_ALLOWED:
            self.send_header("Allow", "GET, HEAD")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def send_json(self, payload: dict, status=HTTPStatus.OK):
        self.send_content(
            json.dumps(
                {"demo": True, "read_only": True, **payload}, ensure_ascii=False
            ).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def do_GET(self):
        request = urlsplit(self.path)
        path = request.path
        if path.startswith("/api/"):
            endpoint = path.removeprefix("/api/")
            if endpoint in {
                "mode",
                "relation",
                "profile",
                "refresh_impression",
                "undo",
                "backup",
                "restore",
            }:
                self.reject_write()
                return
            params = {
                key: values[-1] for key, values in parse_qs(request.query).items()
            }
            try:
                result = self.server.read_api(endpoint, params)
            except (ValueError, TypeError):
                self.send_json(
                    {"success": False, "error": "演示读取参数无效"},
                    HTTPStatus.BAD_REQUEST,
                )
            except KeyError:
                self.send_json(
                    {"success": False, "error": "未知演示读取接口"},
                    HTTPStatus.NOT_FOUND,
                )
            else:
                self.send_json({"success": True, **result})
            return
        if path in {"/", "/index.html"}:
            html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
            html = html.replace(
                "<head>", '<head>\n  <script src="/demo-bridge.js"></script>', 1
            )
            html = html.replace("<body>", "<body>\n" + BANNER, 1)
            html = html.replace("<title>", "<title>演示数据 · ", 1)
            if self.server.vue_js:
                html = html.replace(
                    "https://cdn.jsdelivr.net/npm/vue@3.4.38/dist/vue.global.js",
                    "/vue.global.js",
                )
            self.send_content(html.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/demo-bridge.js":
            self.send_content(BRIDGE.encode("utf-8"), "text/javascript; charset=utf-8")
        elif path in {"/app.js", "/app.css"}:
            mime = "text/javascript" if path.endswith(".js") else "text/css"
            self.send_content(
                (DASHBOARD / path[1:]).read_bytes(), mime + "; charset=utf-8"
            )
        elif path == "/vue.global.js" and self.server.vue_js:
            self.send_content(
                self.server.vue_js.read_bytes(), "text/javascript; charset=utf-8"
            )
        else:
            self.send_json(
                {"success": False, "error": "演示资源不存在"}, HTTPStatus.NOT_FOUND
            )

    do_HEAD = do_GET

    def reject_write(self):
        self.send_json(
            {"success": False, "error": READ_ONLY}, HTTPStatus.METHOD_NOT_ALLOWED
        )

    do_POST = reject_write
    do_PUT = reject_write
    do_PATCH = reject_write
    do_DELETE = reject_write
    do_OPTIONS = reject_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="本机端口，默认 8765")
    parser.add_argument(
        "--vue-js", type=Path, help="可选，本机 Vue 3.4.38 vue.global.js，用于离线渲染"
    )
    args = parser.parse_args()
    with PreviewServer(args.port, args.vue_js) as server:
        url = f"http://localhost:{server.server_port}/"
        print(
            f"Bond 演示数据，只读预览\n浅色 {url}?theme=light\n深色 {url}?theme=dark",
            flush=True,
        )
        print("Ctrl+C 停止。仅装载原生前端与合成 fixture。", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
