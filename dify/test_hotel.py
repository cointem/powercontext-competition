"""Integration tests against a real, dedicated seekdb database."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import app
import rules


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GAME_TOKEN", "local-test-token")
    with TestClient(app.app, headers={"Authorization": "Bearer local-test-token"}) as client:
        yield client


@pytest.fixture
def player():
    name = "test_" + uuid.uuid4().hex
    yield name
    with app.db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM hotel_turns WHERE player_id=%s", (name,))
        cur.execute("DELETE FROM hotel_players WHERE player_id=%s", (name,))


def turn(client, player, action, request_id=None):
    inputs = {"player_id": player, "action": action, "request_id": request_id or uuid.uuid4().hex}
    response = client.post("/context", json=inputs)
    assert response.status_code == 200, response.text
    payload = json.loads(rules.main(response.text)["commit"])
    saved = client.post("/commit", json=payload)
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_choices_change_ending(client, player):
    for action in ["入住", "帮忙", "探索", "探索", "赴约"]:
        result = turn(client, player, action)
    assert "时光守护者" in result["story"]
    assert result["state"]["scene"] == "结局"
    restarted = turn(client, player, "重新开始")
    assert restarted["state"]["loop"] == 2
    for action in ["入住", "捣乱", "帮忙", "探索", "探索", "赴约"]:
        result = turn(client, player, action)
    assert "独行旅客" in result["story"]


def test_memory_survives_new_client(client, player):
    turn(client, player, "入住")
    turn(client, player, "捣乱")
    with TestClient(app.app, headers=client.headers) as other:
        remembered = turn(other, player, "回忆")
        assert "纸飞机" in remembered["story"]
        assert remembered["state"]["trust"] == -2
        assert turn(other, player, "道歉")["state"]["trust"] == -1


def test_retry_is_idempotent_and_action_collision_rejected(client, player):
    first = turn(client, player, "入住", "retry-1")
    second = turn(client, player, "入住", "retry-1")
    assert first["version"] == second["version"] == 1
    assert second["replayed"] is True
    assert client.post("/context", json={"player_id": player, "request_id": "retry-1", "action": "捣乱"}).status_code == 409


def test_concurrent_turns_do_not_overwrite(client, player):
    turn(client, player, "入住")
    payloads = []
    for action in ["帮忙", "捣乱"]:
        response = client.post("/context", json={"player_id": player, "request_id": uuid.uuid4().hex, "action": action})
        payloads.append(json.loads(rules.main(response.text)["commit"]))
    with ThreadPoolExecutor(2) as pool:
        statuses = list(pool.map(lambda payload: client.post("/commit", json=payload).status_code, payloads))
    assert sorted(statuses) == [200, 409]


def test_no_reward_farming_and_no_premature_ending(client, player):
    assert turn(client, player, "赴约")["state"]["scene"] == "门厅"
    turn(client, player, "入住")
    first = turn(client, player, "帮忙")
    second = turn(client, player, "帮忙")
    assert first["state"] == second["state"]
    assert turn(client, player, "赴约")["state"]["scene"] != "结局"


def test_authentication_and_model_free_narration(client, player):
    assert client.post("/context", headers={"Authorization": "Bearer wrong"}, json={"player_id": player, "request_id": "auth", "action": "入住"}).status_code == 401
    result = turn(client, player, "入住")
    response = client.post("/narrate", json={k: result[k] for k in ["story", "reply", "state"]})
    assert response.status_code == 200
    assert response.json()["reply"] == result["reply"]
