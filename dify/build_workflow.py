"""Generate the importable Dify DSL from the checked-in Code node sources."""
from pathlib import Path
import yaml

ROOT = Path(__file__).parent
ACTIONS = ["入住", "帮忙", "捣乱", "道歉", "探索", "赴约", "回忆", "重新开始"]


def build():
    nodes = []

    def node(id, kind, title, **values):
        nodes.append({"id": id, "type": "custom", "position": {"x": len(nodes) * 300, "y": 100},
                      "width": 244, "height": 100, "sourcePosition": "right", "targetPosition": "left",
                      "data": {"type": kind, "title": title, "desc": "", **values}})

    def code(id, title, source, inputs, outputs):
        node(id, "code", title, code_language="python3", code=source,
             variables=[{"variable": key, "value_selector": selector} for key, selector in inputs.items()],
             outputs={key: {"type": "string", "children": None} for key in outputs})

    def http(id, title, endpoint, selector):
        node(id, "http-request", title, method="post", url="{{#env.GAME_URL#}}" + endpoint,
             authorization={"type": "no-auth"}, headers="Content-Type:application/json\nAuthorization:Bearer {{#env.GAME_TOKEN#}}",
             params="", body={"type": "raw-text", "data": [{"key": "", "type": "text", "value": "{{#" + selector + "#}}"}]},
             timeout={"connect": 10, "read": 60, "write": 20},
             retry_config={"retry_enabled": False, "max_retries": 1, "retry_interval": 1000})

    node("start", "start", "选择你的行动", variables=[
        {"variable": "player_id", "label": "旅客代号（同一代号继续剧情）", "type": "text-input", "required": True, "max_length": 64},
        {"variable": "action", "label": "行动", "type": "select", "options": ACTIONS, "required": True},
        {"variable": "request_id", "label": "请求编号（通常留空）", "type": "text-input", "required": False, "max_length": 64},
    ])
    code("prepare", "准备本轮请求", '''import json, re, uuid
def main(player_id: str, action: str, request_id: str = "") -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", player_id):
        raise ValueError("旅客代号使用1到64位字母、数字、下划线或短横线")
    return {"body": json.dumps({"player_id": player_id, "action": action, "request_id": request_id or uuid.uuid4().hex}, ensure_ascii=False)}
''', {"player_id": ["start", "player_id"], "action": ["start", "action"], "request_id": ["start", "request_id"]}, ["body"])
    http("context", "从 seekdb 读取记忆", "/context", "prepare.body")
    code("rules", "决定剧情与 NPC 态度", (ROOT / "rules.py").read_text(), {"context": ["context", "body"]}, ["commit"])
    http("save", "写入 seekdb 并保存本轮", "/commit", "rules.commit")
    code("narration_input", "准备叙事", '''import json
def main(body: str, status_code: int) -> dict:
    if status_code != 200:
        raise ValueError("本轮未保存，请重试：" + body)
    result = json.loads(body)
    return {"body": json.dumps({k: result[k] for k in ("story", "reply", "state")}, ensure_ascii=False)}
''', {"body": ["save", "body"], "status_code": ["save", "status_code"]}, ["body"])
    http("narrate", "叙述这个夜晚", "/narrate", "narration_input.body")
    code("format", "展示剧情和可选行动", '''import json
def main(body: str, status_code: int) -> dict:
    if status_code != 200:
        raise ValueError("叙事服务异常：" + body)
    result = json.loads(body)
    return {"reply": result["reply"], "mode": result["mode"]}
''', {"body": ["narrate", "body"], "status_code": ["narrate", "status_code"]}, ["reply", "mode"])
    node("end", "end", "旅馆的回应", outputs=[{"variable": k, "value_selector": ["format", k], "value_type": "string"} for k in ["reply", "mode"]])
    edges = [{"id": a["id"] + "-" + b["id"], "type": "custom", "source": a["id"], "target": b["id"],
              "sourceHandle": "source", "targetHandle": "target", "data": {"sourceType": a["data"]["type"], "targetType": b["data"]["type"], "isInLoop": False, "isInIteration": False}}
             for a, b in zip(nodes, nodes[1:])]
    return {"version": "0.7.0", "kind": "app", "dependencies": [],
            "app": {"name": "时空旅馆：阿零记得你", "description": "玩家选择改变NPC态度和结局，seekdb跨会话保存记忆。支持规则剧情及可选模型叙事。", "mode": "workflow", "icon": "🌙", "icon_background": "#E4D7F5", "use_icon_as_answer_icon": False},
            "workflow": {"conversation_variables": [], "environment_variables": [
                {"id": "53edfb19-b627-4d21-9402-2384248c4e66", "name": "GAME_URL", "value": "http://host.docker.internal:18089", "value_type": "string", "description": "状态服务地址"},
                {"id": "986e73dd-4c33-4149-a28b-9138ca0be3fe", "name": "GAME_TOKEN", "value": "", "value_type": "secret", "description": "与状态服务GAME_TOKEN一致"}],
            "features": {"file_upload": {"enabled": False}, "opening_statement": "", "retriever_resource": {"enabled": False}, "sensitive_word_avoidance": {"enabled": False}, "speech_to_text": {"enabled": False}, "suggested_questions": [], "suggested_questions_after_answer": {"enabled": False}, "text_to_speech": {"enabled": False}},
            "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.6}}}}


if __name__ == "__main__":
    (ROOT / "time-hotel.yml").write_text(yaml.safe_dump(build(), allow_unicode=True, sort_keys=False), encoding="utf-8")
