"""Import into a local Dify and exercise the REAL workflow and PowerContext.

Credentials are read from explicit local files and never copied into evidence.
"""
import argparse
import base64
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:15001/console/api")
    parser.add_argument("--credentials-file", required=True, type=Path)
    parser.add_argument("--pc-token-file", type=Path, default=ROOT / ".data/runtime.json")
    parser.add_argument("--pc-url", default="http://host.docker.internal:18092")
    parser.add_argument("--configure-web", action="store_true", help="Publish the local app and configure the private web gateway")
    args = parser.parse_args()
    auth = json.loads(args.credentials_file.read_text())
    token = json.loads(args.pc_token_file.read_text())["token"]
    client = httpx.Client(base_url=args.base, timeout=100)
    response = client.post("/login", json={**auth, "password": base64.b64encode(auth["password"].encode()).decode(), "remember_me": False})
    response.raise_for_status()
    assert response.json()["result"] == "success", "Dify login failed"
    for cookie in client.cookies.jar:
        if "csrf" in cookie.name:
            client.headers["X-CSRF-Token"] = cookie.value
    document = yaml.safe_load((ROOT / "grudge-inn-rules.yml").read_text())
    for variable in document["workflow"]["environment_variables"]:
        variable["value"] = token if variable["name"] == "PC_TOKEN" else args.pc_url
    id_file = ROOT / ".data/local-app-id.txt"
    if id_file.exists():
        app_id = id_file.read_text().strip()
        draft = client.get(f"/apps/{app_id}/workflows/draft")
        draft.raise_for_status()
        payload = {k: document["workflow"][k] for k in ("graph", "features", "conversation_variables")}
        payload["environment_variable_patch"] = {"environment_variables": document["workflow"]["environment_variables"], "deleted_environment_variable_ids": []}
        payload["hash"] = draft.json()["hash"]
        response = client.post(f"/apps/{app_id}/workflows/draft", json=payload)
        if response.status_code != 200:
            raise RuntimeError(response.text.replace(token, "[REDACTED]")[:1200])
        response.raise_for_status()
    else:
        response = client.post("/apps/imports", json={"mode": "yaml-content", "yaml_content": yaml.safe_dump(document, allow_unicode=True)})
        response.raise_for_status()
        imported = response.json()
        if imported.get("status") == "pending":
            response = client.post("/apps/imports/" + imported["id"] + "/confirm")
            response.raise_for_status()
            imported = response.json()
        app_id = imported["app_id"]
        id_file.parent.mkdir(exist_ok=True)
        id_file.write_text(app_id)
    print("Local Dify app:", app_id, flush=True)
    evidence = {"timestamp": datetime.now(timezone.utc).isoformat(), "dify": "1.18.0", "powercontext": "1.4.0",
                "app_id": app_id, "mode": "规则试玩", "validation_scope": "Local DSL excludes the LLM branch because this local Dify lacks a plugin daemon. Canonical Cloud DSL retains it.", "runs": []}
    evidence_file = ROOT / "evidence/local-dify-runs.json"
    player = "test_" + secrets.token_hex(8)

    def run(action, save_id="vip", request_id=None, expected=None, player_id=player):
        inputs = {"player_id": player_id, "save_id": save_id, "action": action, "line": "",
                  "request_id": request_id or secrets.token_hex(8), "performance": "规则试玩"}
        response = client.post(f"/apps/{app_id}/workflows/draft/run", json={"inputs": inputs, "files": []})
        response.raise_for_status()
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        finished = next((e for e in events if e.get("event") == "workflow_finished"), {})
        data = finished.get("data", {})
        nodes = [{"id": e["data"].get("node_id"), "status": e["data"].get("status"), "error": e["data"].get("error")}
                 for e in events if e.get("event") == "node_finished"]
        # Keep the actual PC response bodies as receipts, not HTTP headers or credentials.
        receipts = {e["data"]["node_id"]: e["data"].get("outputs", {}).get("body")
                    for e in events if e.get("event") == "node_finished" and e["data"].get("node_id") in ("recall", "save")}
        evidence["runs"].append({"action": action, "save_id": save_id, "status": data.get("status"),
            "outputs": data.get("outputs"), "nodes": nodes, "memory_receipts": receipts})
        evidence_file.write_text(json.dumps(evidence, ensure_ascii=False, indent=2).replace(token, "[REDACTED]"))
        assert data.get("status") == "succeeded", json.dumps({"status": data.get("status"), "error": data.get("error"), "nodes": nodes}, ensure_ascii=False)
        output = data["outputs"]
        if expected:
            assert expected in output["reply"], output
        print(save_id, action, "OK", output.get("ending", ""), flush=True)
        return output

    run("进店看看", expected="行动 0/6")
    run("冒充食评家")
    run("借二十文", request_id="borrow_once", expected="欠款 20 文")
    run("进店看看", expected="欠款还在")  # A new Dify run resumes the same save.
    run("借二十文", request_id="borrow_once", expected="重放")
    run("帮跑堂", expected="行动 3/6")
    run("帮跑堂", expected="行动 3/6")
    run("归还借款", expected="欠款 0 文")
    run("进店看看", expected="钱已经还清")
    run("坦白道歉")
    run("找神秘客", expected="贵宾入住")
    run("帮掌柜", expected="行动 6/6")
    run("回忆旧账", expected="贵宾入住")
    run("帮掌柜", "work")
    run("申请入住", "work", expected="打工抵债")
    run("冒充食评家", "out")
    run("借二十文", "out")
    run("申请入住", "out", expected="被赶出门")
    run("进店看看", "fresh", expected="欠款 0 文")
    isolated = run("回忆旧账", player_id="other_" + secrets.token_hex(8), expected="没有召回记录")
    assert "玩家向跑堂借了" not in isolated["reply"]
    evidence["checks"] = ["三种结局", "新Dify运行恢复旧账", "重复请求不重复借款", "不能刷工钱", "已还款状态", "六回合封盘", "存档隔离", "玩家隔离"]
    evidence["status"] = "passed"
    evidence_file.write_text(json.dumps(evidence, ensure_ascii=False, indent=2).replace(token, "[REDACTED]"))
    print("All", len(evidence["runs"]), "Dify workflow runs passed.", flush=True)
    if args.configure_web:
        response = client.post(f"/apps/{app_id}/workflows/publish", json={})
        response.raise_for_status()
        config_path = ROOT / ".data/web-config.json"
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
        if config.get("app_id") != app_id or not config.get("api_key"):
            response = client.post(f"/apps/{app_id}/api-keys")
            response.raise_for_status()
            config = {"api_key": response.json()["token"]}
        config.update(app_id=app_id, api_base=args.base.removesuffix("/console/api") + "/v1", mode="规则试玩")
        config_path.touch(mode=0o600, exist_ok=True)
        config_path.chmod(0o600)
        config_path.write_text(json.dumps(config))
        print("Local workflow published. Web gateway configured (key kept server-side).", flush=True)


if __name__ == "__main__":
    main()
