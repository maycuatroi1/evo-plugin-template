"""The backend of lumen-hello: plugin/1 on stdin and stdout, standard library only.

The hub starts this program and speaks JSON-RPC 2.0 to it, one JSON object per line: its requests on stdin, the
answers on stdout. Only answers go to stdout; logs go to stderr. The methods (docs/plugins.md of evo-agents, section
The plugin/1 protocol):

- ``initialize``: the plugin's config, of the hub (``instance``) and of each project that enabled it (``projects``);
  the answer names plugin/1 and lists the MCP tools of the manifest;
- ``route``: a call to /v1/x/lumen-hello/<project>/waves, GET or POST;
- ``tool``: a call to the MCP tool lumen-hello__greet;
- ``ping`` and ``shutdown``.

The waves are records of the hub, collection ``waves`` of the project (docs/plugins.md of evo-agents, section Records):
they sit in the hub's Postgres and its backup, not in this process or its data directory, so a restart or a new host
loses nothing. The backend reads and writes them for the person whose call it serves: it sends its own token
(``hub.token`` of ``initialize``) and ``caller.token`` of the call as ``X-Evo-On-Behalf-Of``, so the hub applies that
person's grant. The level and the audience of a wave come from the config (``wave_level``, ``wave_audience``). The
page lists and adds waves through the bridge (``records.list``, ``records.put``) and needs none of this.
"""

from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

PROTOCOL = "plugin/1"
MANIFEST = Path(__file__).resolve().parent.parent / "evo-plugin.json"
MAX_WAVES = 20
COLLECTION = "waves"
TIMEOUT = 10  # seconds for one call to the hub
KEY_BASE = 10**13  # keys count down from it, so the newest wave sorts first
MAX_NAME = 80
DEFAULT_GREETING = "Hello"
INVALID_PARAMS, METHOD_NOT_FOUND, PARSE_ERROR = -32602, -32601, -32700


class Refused(Exception):
    """A call the backend refuses, answered as a JSON-RPC error with ``code``."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def tools_of(manifest: dict) -> list[dict]:
    """The MCP tools the manifest contributes, as initialize lists them."""
    return [
        {
            "name": item["name"],
            "description": item.get("description", ""),
            "input_schema": item.get("input_schema", {"type": "object"}),
        }
        for item in manifest.get("contributes", {}).get("mcp_tools", [])
    ]


class HubRefused(Exception):
    """The hub answered a records call with ``status``: 403 above the caller's level, 404 for a record not seen."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


class Plugin:
    """The plugin's state: its config and how to reach the hub. The waves are the hub's records."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.config: dict = {"instance": {}, "projects": {}}
        self.hub: dict = {}

    def setting(self, project: str, name: str, default: str) -> str:
        layer = self.config.get("projects", {}).get(project) or self.config.get("instance", {})
        return str(layer.get(name) or default)

    def greeting(self, project: str) -> str:
        return self.setting(project, "greeting", DEFAULT_GREETING)

    def records(self, project: str, caller: dict, method: str, path: str = "", query: dict | None = None, body=None):
        """One call of the records API of ``project``, for the person ``caller`` stands for; the JSON answered."""
        url = f"{str(self.hub.get('url', '')).rstrip('/')}/v1/plugin-api/projects/{quote(project, safe='')}/records/"
        url += COLLECTION + path + (f"?{urlencode(query)}" if query else "")
        headers = {"Authorization": f"Bearer {self.hub.get('token', '')}", "Content-Type": "application/json"}
        if caller.get("token"):
            headers["X-Evo-On-Behalf-Of"] = str(caller["token"])
        data = json.dumps(body).encode("utf-8") if body is not None else None
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, data=data, headers=headers, method=method), timeout=TIMEOUT
            ) as answer:
                return json.loads(answer.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                detail = str(json.loads(exc.read()).get("detail"))
            except (ValueError, AttributeError):
                detail = str(exc.reason)
            raise HubRefused(exc.code, detail) from None
        except (urllib.error.URLError, OSError) as exc:
            raise HubRefused(502, f"the hub is not reachable: {exc}") from None

    def waves(self, project: str, caller: dict) -> list[dict]:
        """The latest waves the caller sees, newest first."""
        listed = self.records(project, caller, "GET", query={"limit": MAX_WAVES})
        return [
            {"name": item["body"].get("name", ""), "at": item["body"].get("at", ""), "by": item.get("created_by")}
            for item in ((listed or {}).get("records") or [])[:MAX_WAVES]
        ]

    def initialize(self, params: dict) -> dict:
        self.config = params.get("config") or {"instance": {}, "projects": {}}
        self.hub = params.get("hub") or {}
        plugin = params.get("plugin") or {}
        print(f"lumen-hello {plugin.get('version')}: serving", file=sys.stderr)
        return {
            "protocol": PROTOCOL,
            "plugin": {"id": self.manifest["id"], "version": self.manifest["version"]},
            "tools": tools_of(self.manifest),
        }

    def route(self, params: dict) -> dict:
        if params.get("route") != "waves":
            raise Refused(INVALID_PARAMS, f"no route {params.get('route')!r}")
        caller = params.get("caller") or {}
        project = str(caller.get("project") or "")
        request = params.get("request") or {}
        method = str(request.get("method") or "GET").upper()
        try:
            if method == "GET":
                return self.answer(200, self.board(project, caller))
            if method == "POST":
                try:
                    body = json.loads(base64.b64decode(request.get("body") or "") or b"{}")
                except ValueError:
                    return self.answer(422, {"detail": "the body is not JSON"})
                name = body.get("name") if isinstance(body, dict) else None
                if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
                    return self.answer(422, {"detail": f"name is a text of 1 to {MAX_NAME} characters"})
                self.wave(project, caller, name.strip())
                return self.answer(201, self.board(project, caller))
        except HubRefused as exc:
            return self.answer(exc.status, {"detail": str(exc)})
        return self.answer(405, {"detail": f"{method} is not served here"})

    def wave(self, project: str, caller: dict, name: str) -> None:
        """Keeps a wave as a record, at the level and for the audience the config sets."""
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        key = f"{KEY_BASE - int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"
        record: dict = {"body": {"name": name, "at": at}, "audience": self.setting(project, "wave_audience", "everyone")}
        level = self.setting(project, "wave_level", "")
        if level:
            record["level"] = level
        self.records(project, caller, "PUT", f"/{quote(key, safe='')}", body=record)

    def board(self, project: str, caller: dict) -> dict:
        return {
            "project": project,
            "greeting": self.greeting(project),
            "level": self.setting(project, "wave_level", ""),
            "audience": self.setting(project, "wave_audience", "everyone"),
            "waves": self.waves(project, caller),
        }

    @staticmethod
    def answer(status: int, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return {
            "status": status,
            "headers": {"content-type": "application/json"},
            "body": base64.b64encode(data).decode("ascii"),
        }

    def tool(self, params: dict) -> dict:
        if params.get("tool") != "greet":
            raise Refused(INVALID_PARAMS, f"no tool {params.get('tool')!r}")
        caller = params.get("caller") or {}
        project = str(caller.get("project") or "")
        name = (params.get("arguments") or {}).get("name")
        if not isinstance(name, str) or not name.strip():
            return {"content": [{"type": "text", "text": "name is required"}], "is_error": True}
        try:
            count = len(self.waves(project, caller))
        except HubRefused as exc:
            return {"content": [{"type": "text", "text": f"the hub refused the waves: {exc}"}], "is_error": True}
        text = f"{self.greeting(project)}, {name.strip()}! The page holds {count} wave(s)."
        return {"content": [{"type": "text", "text": text}], "is_error": False}

    def handle(self, method: str, params: dict) -> dict:
        handlers = {
            "initialize": self.initialize,
            "ping": lambda _params: {},
            "shutdown": lambda _params: {},
            "route": self.route,
            "tool": self.tool,
        }
        if method not in handlers:
            raise Refused(METHOD_NOT_FOUND, f"no method {method!r}")
        return handlers[method](params)


def reply(message_id, result=None, error=None) -> None:
    message = {"jsonrpc": "2.0", "id": message_id}
    message.update({"error": error} if error is not None else {"result": result})
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def serve(plugin: Plugin, lines) -> None:
    for line in lines:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply(None, error={"code": PARSE_ERROR, "message": "not JSON"})
            continue
        if not isinstance(message, dict):
            reply(None, error={"code": -32600, "message": "not a request"})
            continue
        method, message_id = message.get("method"), message.get("id")
        try:
            reply(message_id, plugin.handle(str(method), message.get("params") or {}))
        except Refused as exc:
            reply(message_id, error={"code": exc.code, "message": str(exc)})
        except Exception as exc:  # the backend's own failure; it keeps serving
            print(f"lumen-hello: {method} failed: {exc!r}", file=sys.stderr)
            reply(message_id, error={"code": -32603, "message": "internal error"})
        if method == "shutdown":
            return


def main() -> None:
    serve(Plugin(json.loads(MANIFEST.read_text(encoding="utf-8"))), sys.stdin)


if __name__ == "__main__":
    main()
