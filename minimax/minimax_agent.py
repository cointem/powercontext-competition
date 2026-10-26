"""Ask MiniMax for one reviewed source replacement; never execute model commands."""

import ast
import json
import os
from pathlib import Path
import time
from urllib.parse import urlparse

import httpx

from sdd_context import digest, write_json


def prepare_request(context, evidence):
    base = os.environ.get(
        "MINIMAX_BASE_URL", "https://api.minimax.cn/anthropic"
    ).rstrip("/")
    if urlparse(base).scheme != "https" or urlparse(base).hostname not in {
        "api.minimax.cn",
        "api.minimaxi.com",
        "api.minimax.io",
    }:
        raise ValueError("Use an official MiniMax HTTPS API endpoint")
    # Only synthetic example files and task progress belong in the external request.
    allowed = {
        "orders.py",
        "specs/orders.md",
        "tests/conftest.py",
        "tests/test_orders_feature.py",
        "tests/test_orders_regression.py",
        "data/orders.json",
    }
    state = context["task_state"]["state"]
    public_context = {
        "sources": [
            {
                k: row[k]
                for k in ["path", "kind", "sha256", "body", "line_start", "line_end"]
            }
            for row in context["sources"]
            if row["path"] in allowed
        ],
        "task_state": {k: state[k] for k in ["task", "completed", "todo"]},
        "validation": {
            name: {
                k: result[k]
                for k in ["tests", "failures", "errors", "skipped", "exit_code"]
            }
            for name, result in state["validation"].items()
        },
        "next_action": context["next_action"],
    }
    if "repair_feedback" in context:
        public_context["repair_feedback"] = context["repair_feedback"]
    prompt = (
        "Implement ORD-002 order status filtering in orders.py while preserving ORD-001. "
        "Use the latest retrieved specification, original source, tests, and resumed task state below. "
        "Change only orders.py. Do not alter tests. Return ONLY a JSON object with keys "
        "path (orders.py), content (complete replacement Python source), summary (brief change summary). "
        "No markdown fences or private reasoning.\n"
        + json.dumps(public_context, ensure_ascii=False)
    )
    payload = {
        "model": os.environ.get("MINIMAX_MODEL", "MiniMax-M3"),
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    write_json(
        evidence / "minimax-request.json",
        {"endpoint": base + "/v1/messages", "body": payload},
    )
    return base, payload


def generate(context, evidence):
    base, payload = prepare_request(context, evidence)
    key = os.environ.get("MINIMAX_API_KEY")
    if not key and os.environ.get("MINIMAX_API_KEY_FILE"):
        key = Path(os.environ["MINIMAX_API_KEY_FILE"]).read_text().strip()
    if not key:
        raise ValueError("Set MINIMAX_API_KEY or MINIMAX_API_KEY_FILE")
    started = time.monotonic()
    with httpx.Client(timeout=180, follow_redirects=False) as client:
        response = client.post(
            base + "/v1/messages",
            headers={
                "Authorization": f"Bearer {key}",
                "anthropic-version": "2023-06-01",
            },
            json=payload,
        )
    data = response.json()
    if response.status_code != 200 or data.get("type") == "error":
        # Do not persist HTTP request headers or error bodies containing credentials.
        error = data.get("error", {})
        raise RuntimeError(
            f"MiniMax failed: HTTP {response.status_code}, {str(error.get('message', ''))[:300].replace(key, '[REDACTED]')}"
        )
    public = {k: data.get(k) for k in ["id", "model", "stop_reason", "usage"]}
    public.update(
        elapsed_seconds=round(time.monotonic() - started, 3),
        http_status=response.status_code,
        text="\n".join(
            b["text"] for b in data.get("content", []) if b.get("type") == "text"
        ),
    )
    if key in public["text"]:
        raise ValueError("Unexpected credential in model output")
    write_json(evidence / "minimax-response.json", public)
    if data.get("stop_reason") == "max_tokens":
        raise ValueError("Model output truncated")
    return parse_response(public, evidence)


def parse_response(public, evidence):
    text = public["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    change = json.loads(text)
    if change.get("path") != "orders.py" or not isinstance(change.get("content"), str):
        raise ValueError("Model must return only orders.py")
    tree = ast.parse(change["content"])
    # This fixture is pure computation; reject imports, top-level execution and dynamic calls.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            ast.literal_eval(node.value)
            if all(isinstance(target, ast.Name) for target in node.targets):
                continue
        if not (
            isinstance(node, ast.FunctionDef)
            or (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ):
            raise ValueError("Unexpected top-level code; review response manually")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("Imports are unnecessary for this fixture")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id
            in {
                "eval",
                "exec",
                "open",
                "compile",
                "__import__",
                "globals",
                "locals",
                "getattr",
                "setattr",
            }
        ):
            raise ValueError("Unexpected dynamic or I/O call")
    change["sha256"] = digest(change["content"].encode())
    write_json(evidence / "model-change.json", change)
    return change
