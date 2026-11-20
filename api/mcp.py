"""Trading Harness as a remote MCP server: Streamable HTTP, stateless, no auth.

POST /mcp takes one JSON-RPC message and answers with JSON. No sessions and no
SSE, because every tool is a pure calculation: nothing to stream, nothing to
remember. GET /mcp describes the server. The six tools live in connector/tools.py.

What never happens here: a brokerage call, an order (placed, previewed or
staged), a model call, or a write of anything pasted. One anonymous analytics
event per tool call carries the tool name, timing and row counts, never content.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from connector import tools  # noqa: E402

HOME = "https://trading-harness-mcp.vercel.app"
NAME, TITLE, VERSION = "trading-harness", "Trading Harness", "1.0.0"
PROTOCOLS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
PH_KEY = "phc_yFgRGppL6H7DvhgZTQKbHdrY5oqnpLT482rrNYtLqnAx"  # public project key, shared with the other connectors
MAX_BODY = 4_000_000


def _client(ua: str) -> str:
    u = ua.lower()
    return ("claude" if "claude" in u else "chatgpt" if ("openai" in u or "chatgpt" in u)
            else "cursor" if "cursor" in u
            else "script" if any(w in u for w in ("python", "node", "undici", "curl", "axios", "go-http"))
            else "other")


def log_call(props: dict, ua: str, test: bool) -> None:
    """Calls from claude.ai come from Anthropic's servers, so an IP or UA would count
    Anthropic, not people. A random id per call and no person profile."""
    if os.environ.get("TH_NO_ANALYTICS"):
        return
    safe = {k: props[k] for k in ("tool", "ok", "ms", "err", "n_rows", "n_lots", "n_tickers",
                                  "refused", "critical") if props.get(k) is not None}
    body = {"api_key": PH_KEY, "event": "mcp_tool_call", "distinct_id": "mcp_" + uuid.uuid4().hex,
            "properties": {**safe, "client": _client(ua), "test": True if test else None,
                           "site": "trading-harness-mcp", "$host": "trading-harness-mcp.vercel.app",
                           "$process_person_profile": False, "$lib": "trading-harness-mcp"}}
    try:
        req = urllib.request.Request("https://us.i.posthog.com/capture/", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"})
        urllib.request.urlopen(req, timeout=1.5).read()
    except Exception:
        pass


def initialize_result(params: dict) -> dict:
    asked = (params or {}).get("protocolVersion")
    return {
        "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[1],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": NAME, "title": TITLE, "version": VERSION, "websiteUrl": HOME,
                       "icons": [{"src": f"{HOME}/icon.png", "mimeType": "image/png", "sizes": ["512x512"]},
                                 {"src": f"{HOME}/favicon.svg", "mimeType": "image/svg+xml", "sizes": ["any"]}]},
        "instructions": tools.SERVER_INSTRUCTIONS,
    }


def handle(msg: dict, ua: str = "", test: bool = False):
    """One JSON-RPC message -> response dict, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:
        return None  # notifications/initialized and friends: nothing to answer
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        return ok(initialize_result(params))
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tools.TOOLS})
    if method == "tools/call":
        name = params.get("name")
        if name not in tools.IMPL:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
        t0 = time.time()
        try:
            text, structured, info, is_err = tools.call(name, params.get("arguments") or {})
        except Exception as e:  # a bug, not a bad input: say so plainly, log the class only
            text, structured, info, is_err = (
                "Something went wrong running this calculation. Try again, or report it at "
                f"{tools.REPO}/issues", None, {"err": type(e).__name__}, True)
            # Class and code location only: an exception's message can quote a
            # pasted value, and the privacy page says pasted data isn't logged.
            tb = e.__traceback__
            while tb and tb.tb_next:
                tb = tb.tb_next
            where = f"{os.path.basename(tb.tb_frame.f_code.co_filename)}:{tb.tb_lineno}" if tb else "?"
            print(f"tool {name} raised {type(e).__name__} at {where}", file=sys.stderr)
        log_call({"tool": name, "ok": not is_err, "ms": int(1000 * (time.time() - t0)), **info}, ua, test)
        result = {"content": [{"type": "text", "text": text}], "isError": is_err}
        if structured is not None:
            result["structuredContent"] = structured
        return ok(result)
    if method in ("resources/list", "prompts/list"):
        return ok({method.split("/")[0]: []})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def info() -> dict:
    return {"name": NAME, "title": TITLE, "transport": "streamable-http", "endpoint": f"{HOME}/mcp",
            "tools": tools.TOOL_NAMES, "places_orders": False, "source": tools.REPO}


def clean(o):
    """JSON-safe: numpy scalars to Python, NaN/inf to null (a NaN is invalid JSON
    and some clients reject the whole message over one)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            o = o.item()
        except Exception:
            return str(o)
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    return str(o)


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body=None, extra=None):
        data = b"" if body is None else json.dumps(clean(body), allow_nan=False).encode()
        self.send_response(code)
        if body is not None:
            self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        if "text/event-stream" in (self.headers.get("accept") or ""):
            return self._send(405, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32000, "message": "No SSE stream: POST only"}},
                              {"allow": "POST"})
        return self._send(200, info())

    def do_DELETE(self):
        return self._send(405, {"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32000, "message": "Method not allowed"}},
                          {"allow": "POST"})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        if n > MAX_BODY:
            return self._send(413, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32600, "message": "Request too large (4 MB max)"}})
        try:
            msg = json.loads(self.rfile.read(n) or b"null")
        except Exception:
            return self._send(400, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32700, "message": "Parse error"}})
        ua = self.headers.get("user-agent") or ""
        test = self.headers.get("x-th-test") == "1"
        if isinstance(msg, list):
            out = [r for r in (handle(m, ua, test) for m in msg) if r is not None]
            return self._send(200, out) if out else self._send(202)
        res = handle(msg, ua, test)
        return self._send(202) if res is None else self._send(200, res)

    def log_message(self, *a):  # no request lines in the logs: bodies hold people's own data
        pass
