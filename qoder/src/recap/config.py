"""Configuration for Recap.

Two files matter:
- `recap.config.json` (committed): which work traces to read and where data lives.
- `deploy/powercontext.env` (gitignored): PowerContext server URL and bearer token.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "recap.config.json"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


@dataclass
class RepoTrace:
    path: str
    author: str
    name: str | None = None

    @property
    def label(self) -> str:
        return self.name or Path(self.path).name


@dataclass
class Config:
    server_url: str
    token: str
    data_dir: Path
    seekdb_path: Path
    state_path: Path
    pc_env_path: Path
    repos: list[RepoTrace] = field(default_factory=list)
    notes_dirs: list[str] = field(default_factory=list)
    scope_title: str = "Recap 试点"
    scope_summary: str = ""
    embedding_provider: str = "pyseekdb-default"
    base: Path = PROJECT_ROOT

    @property
    def web_dir(self) -> Path:
        return self.base / "web"


def _resolve(value: str, base: Path) -> str:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = base / p
    return str(p)


def load(config_path: Path | None = None) -> Config:
    path = config_path or CONFIG_PATH
    raw = json.loads(path.read_text())
    base = path.parent
    env_file = Path(_resolve(raw.get("powercontext_env", "deploy/powercontext.env"), base))
    env = load_env_file(env_file)

    server_url = os.environ.get("POWERCONTEXT_CLIENT_SERVER_URL") or env.get(
        "POWERCONTEXT_CLIENT_SERVER_URL", "http://127.0.0.1:8100"
    )
    token = (
        os.environ.get("POWERCONTEXT_SERVER_AUTH_TOKEN")
        or env.get("POWERCONTEXT_SERVER_AUTH_TOKEN")
        or env.get("POWERCONTEXT_CLIENT_API_TOKEN")
        or ""
    )
    if not token:
        raise SystemExit(
            f"no PowerContext token: set POWERCONTEXT_SERVER_AUTH_TOKEN in {env_file} "
            "or in the environment"
        )

    data_dir = Path(_resolve(raw.get("data_dir", "data"), base))
    return Config(
        server_url=server_url.rstrip("/"),
        token=token,
        data_dir=data_dir,
        seekdb_path=Path(_resolve(raw.get("seekdb_path", "data/seekdb-recap"), base)),
        state_path=Path(_resolve(raw.get("state_path", "data/state.json"), base)),
        pc_env_path=env_file,
        repos=[RepoTrace(path=_resolve(r["path"], base), author=r["author"], name=r.get("name"))
               for r in raw.get("repos", [])],
        notes_dirs=[_resolve(d, base) for d in raw.get("notes_dirs", [])],
        scope_title=raw.get("scope", {}).get("title", "Recap 试点"),
        scope_summary=raw.get("scope", {}).get("summary", ""),
        embedding_provider=raw.get("embedding_provider", "pyseekdb-default"),
        base=base,
    )
