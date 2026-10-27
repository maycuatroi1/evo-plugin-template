"""Tests of lumen-hello: the backend over plugin/1, as the hub drives it, and the files the hub reads.

Standard library only: `python -m unittest discover -s tests -v`. `evo-agents plugin test .` then runs the
conformance checks a hub relies on (docs/plugins.md of evo-agents, section Writing and checking a plugin).
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "evo-plugin.json").read_text(encoding="utf-8"))
ENTRY = json.loads((ROOT / "market-entry.json").read_text(encoding="utf-8"))


PLUGIN_TOKEN = "evp_test-token"
DELEGATION = "evd_test-delegation"


class Hub:
    """A stand-in for the records API of the hub (docs/plugins.md of evo-agents, Records), in memory, on 127.0.0.1.

    It keeps what the backend PUTs, answers lists in key order as the hub does, and refuses a call that lacks the
    plugin token or whose X-Evo-On-Behalf-Of is not the delegation of the call. It does not model levels: that is the
    hub's own test."""

    def __init__(self):
        self.records: dict[tuple[str, str], dict] = {}
        self.calls: list[tuple[str, str, dict]] = []
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def serve(self):
                size = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(size) if size else b""
                status, payload = hub.handle(self.command, self.path, self.headers, json.loads(raw) if raw else None)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_PUT = serve

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def handle(self, method, path, headers, body):
        parsed = urlparse(path)
        parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
        if parts[:3] != ["v1", "plugin-api", "projects"] or parts[4] != "records":
            return 404, {"detail": "not found"}
        self.calls.append((method, parsed.path, dict(headers)))
        if headers.get("Authorization") != f"Bearer {PLUGIN_TOKEN}":
            return 401, {"detail": "unknown plugin token"}
        sent = headers.get("X-Evo-On-Behalf-Of")
        if sent is not None and sent != DELEGATION:
            return 403, {"detail": "unknown delegation"}
        project, collection = parts[3], parts[5]
        if method == "GET" and len(parts) == 6:
            listed = sorted((k, r) for (p, c, k), r in self.keyed() if (p, c) == (project, collection))
            limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
            return 200, {"records": [dict(r, key=k) for k, r in listed][:limit], "next_after": None}
        if method == "PUT" and len(parts) == 7:
            record = {
                "body": body["body"],
                "level": body.get("level") or "internal",
                "audience": body.get("audience") or "everyone",
                "revision": 1,
                "created_by": "ada" if sent else None,
            }
            self.records[(project, collection, parts[6])] = record
            return 201, dict(record, key=parts[6])
        return 405, {"detail": "method not allowed"}

    def keyed(self):
        return [((p, c, k), r) for (p, c, k), r in self.records.items()]


class Backend:
    """The backend as a process, spoken to as the hub does: one JSON-RPC request per line."""

    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / MANIFEST["backend"]["entry"])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=ROOT,
        )
        self.next = 0

    def send(self, line: str) -> dict:
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next += 1
        return self.send(json.dumps({"jsonrpc": "2.0", "id": self.next, "method": method, "params": params or {}}))

    def close(self) -> None:
        if self.process.poll() is None:
            self.call("shutdown")
            self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


def initialize(backend: Backend, instance: dict | None = None, projects: dict | None = None, hub: str = "") -> dict:
    return backend.call(
        "initialize",
        {
            "protocol": "plugin/1",
            "plugin": {"id": MANIFEST["id"], "version": MANIFEST["version"]},
            "hub": {"url": hub or "http://hub.test", "token": PLUGIN_TOKEN, "version": "0.49.6"},
            "config": {"instance": instance or {}, "projects": projects or {}},
            "grants": {},
        },
    )


def route(backend: Backend, method: str = "GET", body: dict | None = None, project: str = "northwind") -> dict:
    request = {"method": method, "path": "waves"}
    if body is not None:
        request["body"] = base64.b64encode(json.dumps(body).encode()).decode()
    caller = {"login": "ada", "project": project, "token": DELEGATION}
    answer = backend.call("route", {"route": "waves", "request": request, "caller": caller})
    result = answer["result"]
    return {"status": result["status"], "body": json.loads(base64.b64decode(result["body"]))}


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.hub = Hub()
        self.addCleanup(self.hub.close)
        self.backend = Backend()
        self.addCleanup(self.backend.close)

    def start(self, **config):
        return initialize(self.backend, hub=self.hub.url, **config)

    def test_initialize_names_plugin_1_and_the_manifest_tools(self):
        result = self.start()["result"]
        self.assertEqual(result["protocol"], "plugin/1")
        self.assertEqual(result["plugin"], {"id": MANIFEST["id"], "version": MANIFEST["version"]})
        self.assertEqual([tool["name"] for tool in result["tools"]], ["greet"])

    def test_waves_are_records_of_the_hub_not_of_the_process(self):
        self.start(instance={"greeting": "Hello"}, projects={"lumen": {"greeting": "Xin chào"}})
        self.assertEqual(route(self.backend)["body"]["waves"], [])
        posted = route(self.backend, "POST", {"name": " Ada "})
        self.assertEqual(posted["status"], 201)
        self.assertEqual([wave["name"] for wave in posted["body"]["waves"]], ["Ada"])
        self.assertEqual(list(self.hub.records), [("northwind", "waves", next(iter(self.hub.records))[2])])
        # a restarted backend finds them again: nothing was kept in the process
        self.backend.close()
        self.backend = Backend()
        self.start()
        self.assertEqual([wave["name"] for wave in route(self.backend)["body"]["waves"]], ["Ada"])
        other = route(self.backend, project="lumen")["body"]
        self.assertEqual(other["waves"], [])

    def test_every_call_goes_for_the_caller_with_the_plugin_token(self):
        self.start()
        route(self.backend, "POST", {"name": "Ada"})
        route(self.backend)
        self.assertEqual([call[0] for call in self.hub.calls], ["PUT", "GET", "GET"])
        for _method, path, headers in self.hub.calls:
            self.assertTrue(path.startswith("/v1/plugin-api/projects/northwind/records/waves"))
            self.assertEqual(headers["Authorization"], f"Bearer {PLUGIN_TOKEN}")
            self.assertEqual(headers["X-Evo-On-Behalf-Of"], DELEGATION)

    def test_a_wave_takes_the_level_and_audience_of_the_config(self):
        self.start(projects={"northwind": {"wave_level": "internal", "wave_audience": "writer"}})
        route(self.backend, "POST", {"name": "Ada"})
        (record,) = self.hub.records.values()
        self.assertEqual((record["level"], record["audience"]), ("internal", "writer"))
        board = route(self.backend)["body"]
        self.assertEqual((board["level"], board["audience"]), ("internal", "writer"))

    def test_without_a_level_in_the_config_the_hub_picks_the_projects_default(self):
        self.start()
        route(self.backend, "POST", {"name": "Ada"})
        self.assertEqual(self.hub.calls[0][0], "PUT")
        self.assertEqual(route(self.backend)["body"]["audience"], "everyone")

    def test_newest_wave_comes_first_and_the_list_is_capped(self):
        self.start()
        for number in range(25):
            route(self.backend, "POST", {"name": f"member {number}"})
        waves = route(self.backend)["body"]["waves"]
        self.assertEqual(len(waves), 20)
        self.assertEqual(waves[0]["name"], "member 24")

    def test_a_wave_without_a_name_is_refused(self):
        self.start()
        for body in ({}, {"name": "  "}, {"name": "x" * 81}, {"name": 3}):
            self.assertEqual(route(self.backend, "POST", body)["status"], 422)
        self.assertEqual(self.hub.records, {})

    def test_a_hub_that_is_not_reachable_answers_502(self):
        self.start()
        self.backend.call("shutdown")
        self.backend = Backend()
        initialize(self.backend, hub="http://127.0.0.1:9")  # nothing listens
        self.assertEqual(route(self.backend)["status"], 502)

    def test_greet_counts_the_waves_the_caller_sees(self):
        self.start(projects={"northwind": {"greeting": "Hi"}})
        route(self.backend, "POST", {"name": "Ada"})
        caller = {"project": "northwind", "token": DELEGATION}
        answer = self.backend.call("tool", {"tool": "greet", "arguments": {"name": "Grace"}, "caller": caller})["result"]
        self.assertFalse(answer["is_error"])
        self.assertEqual(answer["content"][0]["text"], "Hi, Grace! The page holds 1 wave(s).")

    def test_unknown_methods_and_bad_lines_keep_it_serving(self):
        self.start()
        self.assertEqual(self.backend.call("nothing")["error"]["code"], -32601)
        self.assertEqual(self.backend.send("not json")["error"]["code"], -32700)
        self.assertEqual(self.backend.call("ping")["result"], {})


class FilesTest(unittest.TestCase):
    def test_every_page_entry_and_its_words_exist(self):
        for page in MANIFEST["contributes"]["pages"]:
            entry = ROOT / page["entry"]
            self.assertTrue(entry.is_file(), entry)
            html = entry.read_text(encoding="utf-8")
            self.assertIn("/v1/plugin-ui/kit/1/evo-ui.js", html)
            keys = set(re.findall(r'data-i18n="([a-z_]+)"', html))
            for locale in ("en", "vi"):
                words = (entry.parent / "i18n" / f"{locale}.js").read_text(encoding="utf-8")
                self.assertTrue(words.startswith(f'evo.messages("{locale}"'), locale)
                for key in keys:
                    self.assertRegex(words, rf"\n  {key}: ", f"{locale} lacks {key}")

    def test_the_manifest_asks_for_records_and_the_page_uses_them(self):
        self.assertIn("records:use", MANIFEST["permissions"])
        fields = MANIFEST["config"]["properties"]
        self.assertEqual(fields["wave_audience"]["enum"], ["everyone", "writer"])
        self.assertIn("wave_level", fields)
        script = (ROOT / "pages" / "hello" / "hello.js").read_text(encoding="utf-8")
        for method in ("records.list", "records.put"):
            self.assertIn(f'"{method}"', script)

    def test_the_market_entry_matches_the_manifest(self):
        self.assertEqual(ENTRY["id"], MANIFEST["id"])
        self.assertEqual(ENTRY["publisher"]["id"], MANIFEST["publisher"])
        self.assertEqual(ENTRY["tier"], MANIFEST["tier"])
        self.assertRegex(ENTRY["publisher"]["signer"], r"^sigstore:[^/]+/[^/]+/\.github/workflows/ci\.yml$")
        self.assertIn(f"sigstore:{ENTRY['repo']}/", ENTRY["publisher"]["signer"])


if __name__ == "__main__":
    unittest.main()
