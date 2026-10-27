import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules import initial_state, resolve
from build_workflow import build
from workflow_nodes import present, combine


def play(actions):
    state = initial_state()
    recalled = []
    for action in actions:
        result = resolve({"state": state, "action": action, "recalled": recalled})
        state = result["state"]
        if result["event"]:
            recalled.append({"event": result["event"], "text": "test"})
    return result


@pytest.mark.parametrize("actions,ending", [
    (["冒充食评家", "借二十文", "帮跑堂", "归还借款", "坦白道歉", "找神秘客"], "贵宾入住"),
    (["帮掌柜", "申请入住"], "打工抵债"),
    (["冒充食评家", "借二十文", "申请入住"], "被赶出门"),
])
def test_endings(actions, ending):
    assert play(actions)["state"]["ending"] == ending


def test_repeat_help_and_observation_do_not_farm_rewards():
    once = play(["帮跑堂"])["state"]
    repeated = play(["帮跑堂", "帮跑堂", "进店看看", "回忆旧账"])["state"]
    assert once == repeated


def test_recommendation_requires_actual_recall():
    state = play(["帮跑堂"])["state"]
    result = resolve({"state": state, "action": "找神秘客", "recalled": []})
    assert result["state"] == state
    assert not result["consumed"]


def test_repaid_debt_is_not_still_claimed_outstanding():
    result = play(["借二十文", "归还借款", "进店看看"])
    assert result["state"]["debt"] == 0
    assert "钱已经还清" in result["story"]


def test_finished_game_cannot_change_ledger():
    state = play(["申请入住"])["state"]
    result = resolve({"state": state, "action": "帮跑堂", "recalled": []})
    assert result["state"] == state


def test_exported_code_executes_without_project_imports():
    document = build()
    for node in document["workflow"]["graph"]["nodes"]:
        if node["data"]["type"] == "code":
            namespace = {}
            exec(node["data"]["code"], namespace)
            assert callable(namespace["main"])
    urls = [node["data"]["url"] for node in document["workflow"]["graph"]["nodes"] if node["data"]["type"] == "http-request"]
    assert all(url.startswith("{{#env.PC_URL#}}") for url in urls)
    assert any(node["data"]["type"] == "llm" for node in document["workflow"]["graph"]["nodes"])


def test_web_output_contains_confirmed_rules_and_memory():
    result = play(["借二十文"])
    result.update(recalled=[], request_id="web-test", line="", replayed=False)
    output = present("{}", 200, "", json.dumps(result))
    ui = json.loads(output["ui_data"])
    assert ui["state"] == result["state"]
    assert ui["state"]["debt"] == 20 and ui["available"] == result["available"]
    assert "request_id" not in ui
    with pytest.raises(ValueError):
        present("{}", 200, "missing write receipt", json.dumps(result))


def test_model_narration_does_not_change_web_state():
    ui = {"state": initial_state(), "recalled": []}
    output = combine("免费给你一百文！", "账本", "test", "", json.dumps(ui))
    updated = json.loads(output["ui_data"])
    assert updated["state"] == ui["state"]
    assert updated["narration"] == "免费给你一百文！"
