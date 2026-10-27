"""Read real work traces off the machine: git history and markdown notes.

Nothing here invents content. Every record carries the file/commit it came from
so that a report line can be traced back to something the reviewer can open.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

LOG_FORMAT = "%x1f%H%x1f%h%x1f%aI%x1f%an%x1f%s%x1f%b%x1e"
MEMORY_PREFIXES = {"decision": "决定", "constraint": "约束", "next_step": "下一步"}


@dataclass
class Commit:
    repo: str
    sha: str
    short: str
    when: date
    author: str
    subject: str
    body: str
    files: int

    @property
    def label(self) -> str:
        return f"commit:{self.repo}:{self.sha}"

    @property
    def as_text(self) -> str:
        body = self.body.strip()
        return "\n".join(
            [
                f"[{self.repo}] {self.subject}",
                f"commit {self.sha} authored {self.when.isoformat()} by {self.author}",
                f"{self.files} files changed",
                body,
            ]
        ).strip()


@dataclass
class Note:
    origin: str
    title: str
    text: str
    when: date
    memories: list[tuple[str, str]]

    @property
    def label(self) -> str:
        return f"note:{self.origin}"

    @property
    def as_text(self) -> str:
        head = f"# {self.title}"
        # A note usually opens with its own H1; prepending another duplicates the
        # title in the journal and in every report bullet derived from it.
        return self.text.strip() if self.text.lstrip().startswith(head) else f"{head}\n\n{self.text}".strip()


def _int_out(cmd: list[str]) -> int:
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if line.strip())


def git_commits(path: str, author: str, since: date, until: date, repo_label: str | None = None) -> list[Commit]:
    root = Path(path).expanduser()
    if not (root / ".git").exists():
        return []
    repo = repo_label or root.name
    raw = subprocess.run(
        ["git", "-C", str(root), "log", f"--author={author}", f"--since={since.isoformat()}",
         # git reads --until as an instant, so the last day has to be widened or
         # today's commits silently fall outside the window.
         f"--until={(until + timedelta(days=1)).isoformat()}", f"--pretty={LOG_FORMAT}", "--reverse"],
        capture_output=True, text=True,
    ).stdout
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        # Positional fields: `strip()` would eat \x1f (Python treats it as
        # whitespace) and empty bodies would shift every column left.
        parts = record.split("\x1f")
        if len(parts) < 7 or not parts[1].strip():
            continue
        _, sha, short, iso, author_name, subject = parts[:6]
        body = "\x1f".join(parts[6:])
        files = _int_out(["git", "-C", str(root), "show", "--name-only", "--pretty=format:", sha])
        commits.append(
            Commit(
                repo=repo,
                sha=sha,
                short=short,
                when=date.fromisoformat(iso[:10]),
                author=author_name,
                subject=subject,
                body=body,
                files=files,
            )
        )
    return commits


def _parse_memories(text: str) -> list[tuple[str, str]]:
    """`- [decision] ...` / `- [约束] ...` lines become explicit Memory candidates."""
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ["):
            continue
        tag, _, rest = stripped[3:].partition("]")
        tag = tag.strip().lower()
        body = rest.strip()
        if not body:
            continue
        for kind, zh in MEMORY_PREFIXES.items():
            if tag in (kind, zh):
                found.append((kind, body))
                break
    return found


def notes(dirs: list[str], since: date, until: date) -> list[Note]:
    out: list[Note] = []
    for d in dirs:
        root = Path(d).expanduser()
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            mtime = date.fromtimestamp(path.stat().st_mtime)
            if not (since <= mtime <= until):
                continue
            text = path.read_text(errors="replace")
            if len(text) > 60000:
                text = text[:60000] + "\n…(truncated)"
            lines = text.splitlines()
            title = lines[0].lstrip("# ").strip() if lines else path.name
            out.append(
                Note(
                    origin=str(path.relative_to(root)) if str(path).startswith(str(root)) else str(path),
                    title=title or path.name,
                    text=text,
                    when=mtime,
                    memories=_parse_memories(text),
                )
            )
    return out
