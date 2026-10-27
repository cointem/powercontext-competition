"""Static site + credential-only Dify API gateway. No game rules or memory store.

Run: python3 web/serve.py  (http://localhost:18093)
Deploy: set DIFY_API_BASE, DIFY_API_KEY, INN_MODE, HOST, PORT behind HTTPS.
The default listener is deliberately local. Public hosting also needs user auth.
"""
import json
import mimetypes
import os
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT.parent / ".data/web-config.json"


def configuration():
    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    return {
        "api_base": os.environ.get("DIFY_API_BASE", config.get("api_base", "")),
        "api_key": os.environ.get("DIFY_API_KEY", config.get("api_key", "")),
        "mode": os.environ.get("INN_MODE", config.get("mode", "规则试玩")),
    }


def validate_input(data):
    if not isinstance(data, dict):
        raise ValueError("请求格式无效")
    for name, minimum, maximum in (("player_id", 8, 64), ("save_id", 1, 32), ("request_id", 1, 64)):
        if not re.fullmatch(r"[A-Za-z0-9_-]{%d,%d}" % (minimum, maximum), str(data.get(name, ""))):
            raise ValueError("存档标识无效，请重新打开页面")
    if not isinstance(data.get("action"), str) or not 1 <= len(data["action"]) <= 32:
        raise ValueError("行动无效")
    if not isinstance(data.get("line", ""), str) or len(data.get("line", "")) > 300:
        raise ValueError("台词最多300字")
    return {key: data.get(key, "") for key in ("player_id", "save_id", "request_id", "action", "line")}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, Handler)
        self.inflight = set()
        self.guard = threading.Lock()
        self.capacity = threading.BoundedSemaphore(8)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Do not log input, player credentials, upstream response bodies or keys.
        pass

    def respond(self, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/config":
            config = configuration()
            return self.respond(200, {"configured": bool(config["api_key"] and config["api_base"]), "mode": config["mode"]})
        root = (ROOT / "dist").resolve()
        candidate = (root / unquote(path).lstrip("/")).resolve()
        if candidate == root:
            candidate /= "index.html"
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return self.respond(404, {"error": "页面不存在"})
        return self.respond(200, candidate.read_bytes(), mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")

    def do_POST(self):
        if self.path != "/api/turn":
            return self.respond(404, {"error": "接口不存在"})
        # Browser clients must originate from this exact site, never another site.
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self.respond(403, {"error": "请从游戏页面发起行动"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.respond(415, {"error": "请求格式无效"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError("请求过长或为空")
            inputs = validate_input(json.loads(self.rfile.read(length)))
        except (ValueError, UnicodeDecodeError):
            return self.respond(400, {"error": "请求格式无效，请重新打开页面"})
        config = configuration()
        if not config["api_key"] or not config["api_base"]:
            return self.respond(503, {"error": "客栈尚未连接 Dify，请先配置服务"})
        scope = (inputs["player_id"], inputs["save_id"])
        with self.server.guard:
            if scope in self.server.inflight:
                return self.respond(409, {"error": "这一局仍有行动正在处理，请稍候重试"})
            if not self.server.capacity.acquire(blocking=False):
                return self.respond(503, {"error": "客栈正在忙，请稍候重试"})
            self.server.inflight.add(scope)
        try:
            inputs["performance"] = config["mode"]
            body = json.dumps({"inputs": inputs, "response_mode": "blocking", "user": inputs["player_id"]}).encode()
            request = urllib.request.Request(config["api_base"].rstrip("/") + "/workflows/run", data=body,
                headers={"Authorization": "Bearer " + config["api_key"], "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.load(response)
            data = result.get("data", {})
            if data.get("status") != "succeeded":
                return self.respond(502, {"error": "Dify 尚未确认本轮完成。请重试本轮；会沿用原编号，避免重复记账。"})
            output = data.get("outputs", {})
            ui = json.loads(output["ui_data"])
            if not isinstance(ui.get("state"), dict) or not isinstance(ui.get("available"), list):
                raise ValueError("Invalid workflow output")
            return self.respond(200, {"game": ui, "request_id": output["request_id"], "run_id": result.get("workflow_run_id", "")})
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            return self.respond(502, {"error": "暂时联系不上客栈。进度可能已保存，请重试本轮以确认结果。"})
        finally:
            with self.server.guard:
                self.server.inflight.discard(scope)
                self.server.capacity.release()


if __name__ == "__main__":
    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "18093"))
    server = Server((host, port))
    print(f"Local: http://localhost:{port}", flush=True)
    server.serve_forever()
