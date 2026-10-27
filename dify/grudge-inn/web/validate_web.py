"""Exercise the running web gateway against the real published Dify workflow."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = "http://localhost:18093"
player = uuid.uuid4().hex
runs = []


def request(path, data=None, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = Request(BASE + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        with urlopen(req, timeout=130) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def turn(action, request_id=None, save_id="gateway_test"):
    payload = {"player_id": player, "save_id": save_id, "action": action, "line": "", "request_id": request_id or uuid.uuid4().hex}
    status, body = request("/api/turn", payload)
    assert status == 200, (status, body)
    result = json.loads(body)
    assert result["request_id"] == payload["request_id"]
    runs.append({"action": action, "run_id": result["run_id"], "state": result["game"]["state"], "replayed": result["game"]["replayed"]})
    return result["game"]


if __name__ == "__main__":
    assert request("/")[0] == 200
    status, body = request("/api/config")
    assert status == 200 and "api_key" not in body.decode()
    assert request("/.data/web-config.json")[0] == 404
    assert request("/%2e%2e/.data/web-config.json")[0] == 404
    assert request("/api/turn", {"invalid": True})[0] == 400
    assert request("/api/turn", {}, "https://unrelated.example")[0] == 403
    assert turn("进店看看")["state"]["round"] == 0
    borrowed = turn("借二十文", "borrow-once")
    assert borrowed["state"]["coins"] == 30 and borrowed["state"]["debt"] == 20
    replayed = turn("借二十文", "borrow-once")
    assert replayed["replayed"] and replayed["state"] == borrowed["state"]
    restored = turn("进店看看")
    assert restored["state"] == borrowed["state"] and "欠款还在" in restored["story"]
    turn("帮跑堂")
    recalled = turn("回忆旧账")
    assert "找神秘客" in recalled["available"] and any(e["event"] == "help_clerk" for e in recalled["recalled"])
    assert turn("进店看看", save_id="another_journey")["state"]["debt"] == 0
    evidence = {"timestamp": datetime.now(timezone.utc).isoformat(), "status": "passed", "runs": runs,
                "checks": ["static page", "key not exposed", "path traversal blocked", "invalid input blocked", "cross-origin blocked", "published Dify API", "idempotent retry", "durable resume", "actual memory recall", "save isolation"]}
    (Path(__file__).resolve().parents[1] / "evidence/web-api-runs.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print("Web gateway passed: 7 real Dify runs plus static, input, origin and credential checks.")
