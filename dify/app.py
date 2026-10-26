"""seekdb persistence and optional LLM narration for the Dify workflow."""
import hmac
import json
import os
import re
from contextlib import asynccontextmanager, contextmanager
from typing import Literal

import httpx
import pymysql
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

DB_NAME = os.getenv("SEEKDB_DATABASE", "dify_time_hotel")
if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", DB_NAME):
    raise ValueError("Invalid SEEKDB_DATABASE")


@contextmanager
def db(database=DB_NAME):
    conn = pymysql.connect(host=os.getenv("SEEKDB_HOST", "127.0.0.1"), port=int(os.getenv("SEEKDB_PORT", "12881")),
                           user=os.getenv("SEEKDB_USER", "root"), password=os.getenv("SEEKDB_PASSWORD", ""),
                           database=database, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                           connect_timeout=10, read_timeout=20, write_timeout=20)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize():
    with db(None) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}`")
    with db() as conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS hotel_players (
            player_id VARCHAR(64) PRIMARY KEY, version BIGINT NOT NULL, state LONGTEXT NOT NULL)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS hotel_turns (
            player_id VARCHAR(64) NOT NULL, request_id VARCHAR(64) NOT NULL,
            version BIGINT NOT NULL, action VARCHAR(32) NOT NULL, payload LONGTEXT NOT NULL,
            PRIMARY KEY(player_id, request_id), INDEX history_idx(player_id, version))""")


@asynccontextmanager
async def lifespan(app):
    if not os.getenv("GAME_TOKEN"):
        raise RuntimeError("Set GAME_TOKEN before starting")
    initialize()
    yield


def authorize(authorization: str = Header(default="")):
    if not hmac.compare_digest(authorization, "Bearer " + os.environ["GAME_TOKEN"]):
        raise HTTPException(401, "Invalid game token")


app = FastAPI(title="时空旅馆 · seekdb 状态服务", lifespan=lifespan)


class Turn(BaseModel):
    player_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    action: str = Field(min_length=1, max_length=32)


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene: Literal["门厅", "档案室", "天台", "结局"]
    trust: int = Field(ge=-3, le=3)
    items: list[Literal["铜钥匙", "星灯"]] = Field(max_length=2)
    flags: list[Literal["修灯", "捣乱", "道歉"]] = Field(max_length=3)
    loop: int = Field(ge=1)


class Commit(Turn):
    expected_version: int = Field(ge=0)
    state: State
    story: str = Field(max_length=10000)
    reply: str = Field(max_length=12000)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@app.get("/health")
def health():
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT VERSION() AS version")
        return {"database": "seekdb", "version": cur.fetchone()["version"]}


@app.post("/context", dependencies=[Depends(authorize)])
def context(turn: Turn):
    initial = {"scene": "门厅", "trust": 0, "items": [], "flags": [], "loop": 1}
    with db() as conn, conn.cursor() as cur:
        cur.execute("INSERT IGNORE INTO hotel_players VALUES (%s,0,%s)", (turn.player_id, json_text(initial)))
        cur.execute("SELECT version,state FROM hotel_players WHERE player_id=%s", (turn.player_id,))
        row = cur.fetchone()
        cur.execute("SELECT payload FROM hotel_turns WHERE player_id=%s AND request_id=%s", (turn.player_id, turn.request_id))
        prior = cur.fetchone()
        if prior:
            replay = json.loads(prior["payload"])
            if replay["action"] != turn.action:
                raise HTTPException(409, "request_id already used for another action")
            return {"replay": replay}
        cur.execute("SELECT action,payload FROM hotel_turns WHERE player_id=%s ORDER BY version DESC LIMIT 3", (turn.player_id,))
        history = [{"action": r["action"], "story": json.loads(r["payload"])["story"][:500]} for r in cur.fetchall()]
    return {**turn.model_dump(), "version": row["version"], "state": json.loads(row["state"]), "history": list(reversed(history))}


@app.post("/commit", dependencies=[Depends(authorize)])
def commit(turn: Commit):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT version FROM hotel_players WHERE player_id=%s FOR UPDATE", (turn.player_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Load context before committing")
        cur.execute("SELECT payload FROM hotel_turns WHERE player_id=%s AND request_id=%s", (turn.player_id, turn.request_id))
        prior = cur.fetchone()
        if prior:
            payload = json.loads(prior["payload"])
            if payload["action"] != turn.action:
                raise HTTPException(409, "request_id already used for another action")
            return {**payload, "version": payload["expected_version"] + 1, "replayed": True}
        if row["version"] != turn.expected_version:
            raise HTTPException(409, "Player changed in another turn; reload context")
        payload = turn.model_dump()
        cur.execute("UPDATE hotel_players SET version=version+1,state=%s WHERE player_id=%s",
                    (json_text(payload["state"]), turn.player_id))
        cur.execute("INSERT INTO hotel_turns VALUES (%s,%s,%s,%s,%s)",
                    (turn.player_id, turn.request_id, turn.expected_version + 1, turn.action, json_text(payload)))
    return {**payload, "version": turn.expected_version + 1, "replayed": False}


class Narration(BaseModel):
    story: str = Field(max_length=10000)
    reply: str = Field(max_length=12000)
    state: State


@app.post("/narrate", dependencies=[Depends(authorize)])
def narrate(turn: Narration):
    model = os.getenv("LLM_MODEL")
    if not model:
        return {"reply": turn.reply, "mode": "规则剧情"}
    try:
        response = httpx.post(os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"]}, timeout=40,
            json={"model": model, "messages": [
                {"role": "system", "content": "你是时空旅馆的叙事者。用80到150字中文重述给定剧情，保留事件、人物态度和结局。不要新增物品、改变信任值或指示用户执行现实操作。"},
                {"role": "user", "content": json_text({"剧情": turn.story, "状态": turn.state.model_dump()})}], "temperature": 0.7})
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Empty narration")
        # The canonical choices and state stay outside model-generated prose.
        return {"reply": text + "\n\n" + turn.reply.split("\n\n")[-2] + "\n\n" + turn.reply.split("\n\n")[-1], "mode": "模型叙事"}
    except (httpx.HTTPError, KeyError, ValueError):
        return {"reply": turn.reply, "mode": "规则剧情（模型暂不可用）"}
