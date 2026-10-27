"""Write real work traces into PowerContext as evidence, contracts and handoffs.

Idempotent by construction: Source identity covers label and content, so
re-running a week replays unchanged records into the same journal positions and
appends a new version only when the underlying trace actually changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from recap import traces
from recap.config import Config
from recap.pctx import PowerContext


@dataclass
class RepoBundle:
    repo: str
    commits: list[traces.Commit] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    handoff_ref: dict | None = None
    outcome_source_id: str | None = None


@dataclass
class IngestResult:
    week: str
    since: date
    until: date
    bundles: list[RepoBundle] = field(default_factory=list)
    memories: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def sources(self) -> int:
        return sum(len(b.source_ids) for b in self.bundles) + len(self.notes)


def week_label(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


async def collect(cfg: Config, pc: PowerContext, scope_id: str, since: date, until: date) -> IngestResult:
    result = IngestResult(week=week_label(until), since=since, until=until)

    for note in traces.notes(cfg.notes_dirs, since, until):
        result.notes.append(await pc.capture(scope_id, note.label, note.as_text, {"kind": "note"}))
        for kind, text in note.memories:
            remembered = await pc.remember_new(scope_id, kind, text, reason=f"from note {note.origin}")
            if remembered:
                result.memories.append(remembered)

    for repo in cfg.repos:
        commits = traces.git_commits(repo.path, repo.author, since, until, repo.label)
        if not commits:
            continue
        bundle = RepoBundle(repo=repo.label, commits=commits)
        for commit in commits:
            bundle.source_ids.append(
                await pc.capture(
                    scope_id,
                    commit.label,
                    commit.as_text,
                    {"kind": "commit", "repo": repo.label, "sha": commit.sha},
                )
            )
        await _declare_contract(pc, scope_id, bundle, since, until, result.week)
        result.bundles.append(bundle)

    if result.bundles:
        await _snapshot_week(pc, scope_id, result)
        for bundle in result.bundles:
            await _record_outcome(pc, scope_id, bundle, result.week)

    return result


async def _declare_contract(
    pc: PowerContext, scope_id: str, bundle: RepoBundle, since: date, until: date, week: str
) -> None:
    repo = bundle.repo
    commits = bundle.commits
    objectives = sorted({c.subject.split(":", 1)[0] for c in commits})
    await pc.work_contract(
        scope_id,
        f"contract:{repo}:{week}",
        objective=f"{week} 推进 {repo}：{'; '.join(objectives[:3])}",
        facts=[{"text": f"{len(commits)} commits by {commits[0].author} between {since} and {until}", "basis": "declared", "evidence": []}],
        in_scope=[c.subject for c in commits[:8]],
        exclusions=["不改动上游 master，只在竞赛分支交付"],
        completion_criteria=["交付物可被评审一条命令复现"],
        open_questions=[c.body.strip().splitlines()[0] for c in commits if c.body.strip()][:2],  # RootModel[str]
    )


async def _snapshot_week(pc: PowerContext, scope_id: str, result: IngestResult) -> None:
    """One boundary per Scope — PowerContext keeps a single Handoff lineage, so the
    weekly snapshot covers every repo instead of minting a revision per repository."""
    week = result.week
    claims = [
        {"text": commit.subject, "basis": "verified", "evidence": [{"source_id": source_id}]}
        for bundle in result.bundles
        for commit, source_id in list(zip(bundle.commits, bundle.source_ids))[:8]
    ]
    newest = max((c for b in result.bundles for c in b.commits), key=lambda c: (c.when, c.short))
    prepared = await pc.snapshot_current_work(
        scope_id,
        f"boundary:{week}",
        objective=f"{week} 当前工作状态",
        claims=claims,
        disposition="continuable",
        next_action={"text": f"继续 {newest.repo}：跟进 {newest.subject}", "basis": "declared"},
        omissions=["未覆盖未提交的本机草稿"],
    )
    current = (await pc.handoffs(scope_id))[:1]
    if current and _already_committed(current[0]["content"], claims, prepared["prepared"]["content"]):
        # An unchanged week must replay into the revision it already produced.
        reference = {k: current[0][k] for k in ("family", "artifact_id", "revision")}
    else:
        committed = await pc.commit_handoff(scope_id, prepared["prepared"], prepared["boundary"]["source_id"])
        reference = committed["reference"]
        await pc.acknowledge(
            scope_id,
            f"ack:{week}",
            reference,
            message=f"{week} 里程碑已确认",
        )
    for bundle in result.bundles:
        bundle.handoff_ref = reference


def _already_committed(content: dict, claims: list[dict], prepared_content: dict) -> bool:
    return (
        _texts(content.get("state")) == _texts(claims)
        and (content.get("next_action") or {}).get("text")
        == (prepared_content.get("next_action") or {}).get("text")
        and _texts(content.get("omissions")) == _texts(prepared_content.get("omissions"))
    )


def _texts(items: Any) -> list[str]:
    return [item.get("text") if isinstance(item, dict) else item for item in items or []]


async def _record_outcome(pc: PowerContext, scope_id: str, bundle: RepoBundle, week: str) -> None:
    repo = bundle.repo
    commits = bundle.commits
    last = bundle.commits[-1]
    outcome = await pc.record_outcome(
        scope_id,
        f"outcome:{repo}:{week}",
        objective=f"{repo} · {week}",
        status="succeeded",
        summary=f"{len(commits)} commits；最新 {last.short} {last.subject}",
        observations=[{"text": c.subject, "basis": "verified", "evidence": [{"kind": "source", "source_ref": {"name": "content", "source_id": sid}}]} for c, sid in zip(bundle.commits, bundle.source_ids)],
        checks=[
            {
                "name": "commit-range",
                "status": "passed",
                "basis": "verified",
                "evidence": [{"kind": "source", "source_ref": {"name": "content", "source_id": bundle.source_ids[0]}}],
            }
        ],
        produced_artifacts=[bundle.handoff_ref],
        remaining_work=[f"跟进 {last.subject}"],
    )
    bundle.outcome_source_id = outcome["source_id"]
