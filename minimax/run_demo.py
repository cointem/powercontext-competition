"""Reproduce the six competition acceptance checks with real services."""

from __future__ import annotations

import argparse
import difflib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

from minimax_agent import generate
from sdd_context import digest, write_json

HERE = Path(__file__).resolve().parent
REQUIRED = {
    "specs/orders.md",
    "orders.py",
    "tests/test_orders_feature.py",
    "tests/test_orders_regression.py",
}


def cli(run, command, name, *extra):
    config = json.loads((run / "run.json").read_text())
    output = run / "evidence" / f"{name}.json"
    cmd = [
        sys.executable,
        str(HERE / "sdd_context.py"),
        command,
        "--root",
        str(run / "workspace"),
        "--project",
        config["project"],
        "--state-dir",
        str(run / "powercontext"),
        "--output",
        str(output),
        *extra,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE, timeout=120)
    (run / "evidence" / f"{name}.stderr.log").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"{command} failed: {result.stderr[-2500:]}")
    return json.loads(output.read_text())


def tests(run, label, filename):
    workspace = run / "workspace"
    report = run / "evidence" / f"{label}.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        f"tests/{filename}",
        f"--junitxml={report}",
    ]
    result = subprocess.run(
        cmd, cwd=workspace, capture_output=True, text=True, timeout=60
    )
    log = result.stdout + result.stderr
    (run / "evidence" / f"{label}.log").write_text(log)
    suites = ET.parse(report).getroot().findall("testsuite")
    summary = {
        key: sum(int(s.get(key, 0)) for s in suites)
        for key in ["tests", "failures", "errors", "skipped"]
    }
    summary.update(
        command=[
            "python",
            "-m",
            "pytest",
            "-q",
            f"tests/{filename}",
            "--junitxml=<evidence>",
        ],
        exit_code=result.returncode,
        code_sha256=digest((workspace / "orders.py").read_bytes()),
        tests_sha256={
            p.name: digest(p.read_bytes())
            for p in sorted((workspace / "tests").glob("*.py"))
        },
    )
    write_json(run / "evidence" / f"{label}.json", summary)
    return summary


def save(run, name, state):
    path = run / "evidence" / f"{name}-input.json"
    write_json(path, state)
    return cli(run, "save", name, "--state", str(path))


def prepare(run):
    if run.exists():
        raise ValueError(
            "Use a new run directory; previous evidence is never overwritten"
        )
    (run / "evidence").mkdir(parents=True)
    workspace = run / "workspace"
    shutil.copytree(
        HERE / "sample",
        workspace,
        ignore=shutil.ignore_patterns("__pycache__", "after"),
    )
    shutil.copyfile(workspace / "before/orders.py", workspace / "orders.py")
    write_json(run / "run.json", {"project": "sdd-" + uuid.uuid4().hex})
    empty = cli(run, "resume", "state-empty")
    assert empty["state"] is None
    imported = cli(run, "import", "import-v1")
    context1 = cli(run, "context", "context-v1")
    assert REQUIRED <= {r["path"] for r in context1["sources"]}
    before = tests(run, "before-feature", "test_orders_feature.py")
    regression = tests(run, "before-regression", "test_orders_regression.py")
    assert (
        before["exit_code"] == 1
        and before["failures"] > 0
        and before["errors"] == 0
        and before["skipped"] == 0
    )
    assert regression["exit_code"] == 0
    save(
        run,
        "state-save-before",
        {
            "sequence": 1,
            "task": "orders status filter",
            "completed": [
                "Imported revision 1",
                "Recorded functional failures and passing regression",
            ],
            "todo": [
                "Import revision 2 with refunded support",
                "Ask MiniMax to modify orders.py",
                "Run unchanged tests",
            ],
            "validation": {"before_feature": before, "before_regression": regression},
        },
    )
    spec = workspace / "specs/orders.md"
    spec.write_text(
        spec.read_text()
        .replace("revision 1", "revision 2")
        .replace(
            "`pending`, `paid`, `shipped`, `cancelled`",
            "`pending`, `paid`, `shipped`, `cancelled`, `refunded`",
        )
        + "\n## ORD-004: Refund support (effective revision 2)\nThe explicit status filter must also accept `refunded`; all ORD-001 behaviors remain unchanged.\n"
    )
    cli(run, "import", "import-v2")
    save(
        run,
        "state-save-ready",
        {
            "sequence": 2,
            "task": "orders status filter",
            "completed": [
                "Recorded baseline failures",
                "Imported revision 2, including ORD-004 refunded support",
            ],
            "todo": [
                "Ask MiniMax to modify orders.py",
                "Run unchanged feature and regression tests",
            ],
            "validation": {"before_feature": before, "before_regression": regression},
        },
    )
    context2 = cli(run, "context", "context-v2")
    assert context2["task_state"]["state"]["sequence"] == 2
    source1 = next(r for r in context1["sources"] if r["path"] == "specs/orders.md")
    source2 = next(r for r in context2["sources"] if r["path"] == "specs/orders.md")
    assert (
        source1["sha256"] != source2["sha256"]
        and "ORD-004" in source2["body"]
        and "revision 1" not in source2["body"]
    )
    assert digest((workspace / "orders.py").read_bytes()) == before["code_sha256"]
    print(
        json.dumps(
            {
                "prepared": str(run),
                "target_failures": before["failures"],
                "regression_passed": regression["tests"],
                "imported": len(imported["documents"]),
            },
            ensure_ascii=False,
        )
    )


def generate_change(run):
    context = cli(run, "context", "context-for-minimax")
    if context["next_action"] != "Ask MiniMax to modify orders.py":
        raise ValueError("Task is not ready for generation")
    feedback = run / "evidence/repair-feedback.json"
    if feedback.exists():
        context["repair_feedback"] = json.loads(feedback.read_text())
    change = generate(context, run / "evidence")
    # Source is staged for explicit review, and applied only by the verify command.
    (run / "evidence" / "generated-orders.py").write_text(change["content"])
    print(
        json.dumps(
            {"generated": "evidence/generated-orders.py", "sha256": change["sha256"]}
        )
    )


def verify(run, recorded=False):
    evidence = run / "evidence"
    if recorded:
        source = HERE / "evidence" / "model-change.json"
        change = json.loads(source.read_text())
        write_json(
            evidence / "replay-origin.json",
            {
                "mode": "recorded MiniMax output; no new API call",
                "source_sha256": digest(source.read_bytes()),
            },
        )
    else:
        change = json.loads((evidence / "model-change.json").read_text())
    assert (
        change["path"] == "orders.py"
        and digest(change["content"].encode()) == change["sha256"]
    )
    before = (run / "workspace/orders.py").read_text()
    assert (
        digest(before.encode())
        == json.loads((evidence / "before-feature.json").read_text())["code_sha256"]
    )
    (run / "workspace/orders.py").write_text(change["content"])
    (evidence / "orders.diff").write_text(
        "".join(
            difflib.unified_diff(
                before.splitlines(True),
                change["content"].splitlines(True),
                fromfile="before/orders.py",
                tofile="after/orders.py",
            )
        )
    )
    feature = tests(run, "after-feature", "test_orders_feature.py")
    regression = tests(run, "after-regression", "test_orders_regression.py")
    for name, result in [("feature", feature), ("regression", regression)]:
        baseline = json.loads((evidence / f"before-{name}.json").read_text())
        assert result["tests_sha256"] == baseline["tests_sha256"]
        assert (
            result["exit_code"] == 0
            and result["skipped"] == 0
            and result["tests"] == baseline["tests"]
        )
    saved = save(
        run,
        "state-save-after",
        {
            "sequence": 3,
            "task": "orders status filter",
            "completed": [
                "MiniMax code applied",
                "All unchanged feature and regression tests passed",
            ],
            "todo": ["Review validated diff for delivery"],
            "validation": {"feature": feature, "regression": regression},
        },
    )
    # Index changed code, then start a separate CLI process to reuse persisted validation.
    cli(run, "import", "import-after")
    resumed = cli(run, "context", "context-resumed")
    assert resumed["task_state"]["pid"] != saved["pid"]
    assert resumed["task_state"]["state"] == saved["saved_state"]
    assert resumed["next_action"] == "Review validated diff for delivery"
    assert REQUIRED <= {r["path"] for r in resumed["sources"]}
    summary = {
        "mode": "recorded-output replay" if recorded else "live MiniMax generation",
        "checks": {
            "runnable": True,
            "minimax_code_matches": change["sha256"] == feature["code_sha256"],
            "context_and_spec_update": True,
            "seekdb_retrieval": True,
            "state_reused_after_restart": True,
            "same_tests_red_to_green": True,
        },
        "before_feature": json.loads((evidence / "before-feature.json").read_text()),
        "after_feature": feature,
        "after_regression": regression,
        "state_write_pid": saved["pid"],
        "state_read_pid": resumed["task_state"]["pid"],
        "python": platform.python_version(),
        "packages": {
            p: importlib.metadata.version(p)
            for p in ["powercontext", "PyMySQL", "httpx", "pytest"]
        },
        "seekdb_version": resumed["server_version"],
    }
    write_json(evidence / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare", "generate", "verify"])
    p.add_argument("--run", type=Path, required=True)
    p.add_argument(
        "--recorded",
        action="store_true",
        help="Verify committed MiniMax output without another API call",
    )
    args = p.parse_args()
    run = args.run.resolve()
    {
        "prepare": prepare,
        "generate": generate_change,
        "verify": lambda r: verify(r, args.recorded),
    }[args.command](run)


if __name__ == "__main__":
    main()
