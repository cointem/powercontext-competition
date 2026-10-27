"""Sources copied into native Dify Code nodes; no game server is required."""
import hashlib
import json
import re
import uuid


def prepare(player_id, save_id, action, line="", request_id=""):
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", player_id):
        raise ValueError("旅客口令须为8至64位字母、数字、下划线或短横线；请使用难猜的随机值")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", save_id):
        raise ValueError("存档号须为1至32位字母、数字、下划线或短横线")
    if request_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
        raise ValueError("请求编号格式无效")
    if len(line or "") > 300:
        raise ValueError("台词最多300字")
    key = hashlib.sha256((player_id + ":" + save_id).encode()).hexdigest()
    return {"scope_body": json.dumps({"title": "这家客栈会记仇", "summary": "Dify客栈游戏的独立存档",
        "idempotency_key": "grudge-inn-v1-" + key}, ensure_ascii=False),
        "input": json.dumps({"action": action, "line": line or "", "request_id": request_id or uuid.uuid4().hex}, ensure_ascii=False)}


def scope_input(body, status_code):
    if status_code not in (200, 201):
        raise ValueError("PowerContext未能打开存档，请检查服务地址与令牌")
    scope = json.loads(body)["scope_id"]
    return {"scope": scope, "list_body": json.dumps({"scope_id": scope, "limit": 100}),
            "recall_body": json.dumps({"scope_id": scope, "query": "innledger", "max_bytes": 32768})}


def game(inventory, inventory_status, memory, memory_status, scope, input):
    # initial_state, resolve and ACTIONS are bundled from rules.py by the generator.
    if inventory_status != 200 or memory_status != 200:
        raise ValueError("读取PowerContext失败，本轮未推进，请恢复记忆服务后重试")
    user = json.loads(input)
    if user["action"] not in ACTIONS:
        raise ValueError("请选择有效行动")
    entries = json.loads(inventory)
    if entries.get("next_cursor"):
        raise ValueError("存档出现过多记录，请换新存档号；不会使用不完整的历史继续")
    snapshots = []
    for record in entries["entries"]:
        if record.get("kind") == "inn_save_v1" and record.get("state") == "active":
            snapshot = json.loads(record["text"].removeprefix("innledger "))
            if snapshot["schema"] != 1 or not 1 <= snapshot["state"]["round"] <= 6:
                raise ValueError("存档版本或回合无效")
            snapshots.append(snapshot)
    snapshots.sort(key=lambda x: x["state"]["round"])
    by_round = {}
    for snapshot in snapshots:
        turn = snapshot["state"]["round"]
        if turn in by_round and snapshot != by_round[turn]:
            raise ValueError("检测到同档并发操作冲突，请换新存档；每个存档请串行游玩")
        by_round[turn] = snapshot
    latest = snapshots[-1] if snapshots else {"state": initial_state(), "events": []}
    for snapshot in snapshots:
        if snapshot["input"]["request_id"] == user["request_id"]:
            if snapshot["input"] != user:
                raise ValueError("请求编号已用于不同输入，请换新编号")
            result = snapshot["result"]
            result["replayed"] = True
            return make_output(result, scope, None)
    prepared = json.loads(memory)
    content = prepared.get("content") or ""
    start = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
    end = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
    recalled_events = []
    if start in content and end in content:
        payload = json.loads(content.split(start, 1)[1].split(end, 1)[0].strip())
        for item in payload.get("items", []):
            value = item.get("content", "")
            if item.get("truncated") or not value.startswith("innledger "):
                continue
            recalled_events.extend(json.loads(value[len("innledger "):]).get("events", []))
    # An event must exist in both the durable save and actual PreparedContext recall.
    recalled = [event for event in latest["events"] if event in recalled_events]
    result = resolve({"state": latest["state"], "action": user["action"], "recalled": recalled})
    result.update(recalled=recalled, request_id=user["request_id"], line=user["line"], replayed=False)
    event_text = {
        "critic": "玩家冒充食评家蹭面，掌柜记住了这个谎言。",
        "borrow": "玩家向跑堂借了二十文，承诺打烊前归还。",
        "help_clerk": "玩家接住跑堂的碗并擦桌，跑堂愿意介绍神秘客。",
        "help_owner": "玩家帮掌柜核对账簿，赢得掌柜信任。",
        "repay": "玩家归还了二十文借款，欠款已结清。",
        "apology": "玩家向掌柜坦白并道歉，不再冒充食评家。",
        "recommend": "神秘客经跑堂介绍赠送推荐帖，愿为玩家担保。",
        "checkout": "玩家申请入住，掌柜根据履约情况结算。"}
    snapshot = None
    if result["consumed"]:
        events = latest["events"] + [{"event": result["event"], "text": event_text[result["event"]]}]
        # Save only authoritative rules and confirmed recall, never generated narration.
        snapshot = {"schema": 1, "state": result["state"], "events": events, "input": user, "result": result}
    return make_output(result, scope, snapshot)


def make_output(result, scope, snapshot):
    text = "innledger " + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) if snapshot else ""
    if len(text.encode()) > 8192:
        raise ValueError("存档超过记忆大小限制，本轮未写入")
    if snapshot:
        path = "/v1/memory/remember"
        payload = {"scope_id": scope, "kind": "inn_save_v1", "text": text, "reason": "Dify游戏规则确认的回合，供跨会话恢复和NPC召回复用"}
    else:
        path = "/v1/context/prepare"
        payload = {"scope_id": scope, "query": "innledger", "max_bytes": 512}
    return {"write_path": path, "write_body": json.dumps(payload, ensure_ascii=False),
            "expected_text": text, "result": json.dumps(result, ensure_ascii=False)}


def present(body, status_code, expected_text, result):
    if status_code != 200:
        raise ValueError("记忆操作未确认成功。请使用原请求编号重试，不要连续点击。")
    saved = json.loads(body)
    if expected_text and not any(r.get("text") == expected_text for r in saved.get("records", [])):
        raise ValueError("PowerContext未返回预期存档，不显示虚假的保存成功")
    result = json.loads(result)
    state = result["state"]
    text = "# 这家客栈会记仇\n\n" + result["story"] + "\n\n"
    if result["recalled"]:
        text += "📖 **NPC 翻出的旧账**\n\n" + "\n".join("- " + item["text"] for item in result["recalled"]) + "\n\n"
    text += (f"**账本** · 行动 {state['round']}/6 · 银两 {state['coins']} 文 · 欠款 {state['debt']} 文 · 信誉 {state['reputation']}\n\n"
             + "**关系** · " + " · ".join(f"{name} {score:+d}" for name, score in state["relationships"].items()) + "\n\n")
    if state["ending"]:
        text += "**本局结局：" + state["ending"] + "**\n\n"
    text += "**可选行动**：" + " / ".join(result["available"]) + "\n\n"
    if result["event"] == "help_clerk":
        text += "💡 下一次选择「找神秘客」，跑堂会先从记忆中回想你的帮助。\n\n"
    if result["replayed"]:
        text += "本次为已保存回合的重放，没有重复扣款或奖励。\n\n"
    return {"reply": text, "prompt": json.dumps({"本轮事实": result["story"], "账本": state,
            "召回的旧账": result["recalled"], "玩家台词": result["line"]}, ensure_ascii=False),
            "request_id": result["request_id"], "ending": state["ending"],
            "ui_data": json.dumps({k: result[k] for k in
                ("state", "story", "event", "consumed", "available", "recalled", "replayed")}, ensure_ascii=False)}


def combine(narration, reply, request_id, ending, ui_data):
    ui = json.loads(ui_data)
    ui["narration"] = narration[:1200]
    narration = narration[:1200].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for char in "\\`*_{}[]()#!":
        narration = narration.replace(char, "\\" + char)
    return {"reply": "🎭 **NPC 即兴对白**\n\n" + narration + "\n\n---\n\n" + reply,
            "request_id": request_id, "ending": ending, "mode": "Dify LLM 即兴演出",
            "ui_data": json.dumps(ui, ensure_ascii=False)}
