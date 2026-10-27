"""Where the config comes from: committed file, gitignored env, then environment."""

import json
from pathlib import Path

import pytest

from recap.config import Config, RepoTrace, load, load_env_file


def write_env(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def make_project(tmp_path: Path, raw: dict | None = None) -> Path:
    write_env(tmp_path / "deploy" / "powercontext.env", [
        "# committed nowhere",
        "POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:8100/",
        'POWERCONTEXT_SERVER_AUTH_TOKEN="secret-token"',
        "ignored-line-without-equals",
    ])
    config = tmp_path / "recap.config.json"
    if raw is None:
        raw = {"repos": [{"path": "../outside", "author": "cointem"}]}
    config.write_text(json.dumps(raw))
    return config


def test_env_file_parsing(tmp_path) -> None:
    env = load_env_file(write_env(tmp_path / "e.env", ["A=1", " # comment", 'B = "two" ', "C='x'", "junk"]))
    assert env == {"A": "1", "B": "two", "C": "x"}
    assert load_env_file(tmp_path / "missing.env") == {}


def test_relative_paths_are_resolved_against_the_config(tmp_path) -> None:
    monkeypatch_config = make_project(tmp_path)
    cfg = load(monkeypatch_config)
    assert cfg.seekdb_path == tmp_path / "data" / "seekdb-recap"
    assert cfg.state_path == tmp_path / "data" / "state.json"
    assert cfg.pc_env_path == tmp_path / "deploy" / "powercontext.env"
    assert cfg.web_dir == tmp_path / "web"
    assert cfg.base == tmp_path
    # The token file is the source of truth; the trailing slash is stripped for URL joins.
    assert cfg.server_url == "http://127.0.0.1:8100"
    assert cfg.token == "secret-token"
    # Relative trace paths are read as written, resolved against the config directory.
    assert Path(cfg.repos[0].path).resolve() == (tmp_path.parent / "outside").resolve()


def test_environment_overrides_the_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "http://other:9000")
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_TOKEN", "env-token")
    cfg = load(make_project(tmp_path))
    assert (cfg.server_url, cfg.token) == ("http://other:9000", "env-token")


def test_missing_token_fails_before_any_request(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("POWERCONTEXT_SERVER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("POWERCONTEXT_CLIENT_SERVER_URL", raising=False)
    config = tmp_path / "recap.config.json"
    config.write_text("{}")
    with pytest.raises(SystemExit, match="no PowerContext token"):
        load(config)


def test_defaults_when_optional_keys_are_absent(tmp_path) -> None:
    cfg = load(make_project(tmp_path, {}))
    assert cfg.scope_title == "Recap 试点"
    assert cfg.embedding_provider == "pyseekdb-default"
    assert cfg.notes_dirs == []
    assert cfg.repos == []


def test_repo_label_prefers_the_configured_name() -> None:
    assert RepoTrace(path="/x/powercontext", author="cointem").label == "powercontext"
    assert RepoTrace(path="/x/powercontext", author="cointem", name="pc-competition").label == "pc-competition"


def test_committed_config_points_at_the_pilot_scope() -> None:
    """The file that ships with the deliverable must stay loadable and free of secrets."""
    project = Path(__file__).resolve().parents[1]
    raw = json.loads((project / "recap.config.json").read_text())
    assert set(raw) <= {
        "powercontext_env", "data_dir", "seekdb_path", "state_path", "scope", "repos",
        "notes_dirs", "embedding_provider",
    }
    assert "token" not in json.dumps(raw).lower()
    assert raw["powercontext_env"] == "deploy/powercontext.env"


def test_config_dataclass_has_no_receiver_left() -> None:
    """The receipt signs as the authenticated Principal, so a configured receiver is dead."""
    assert not hasattr(Config(server_url="", token="", data_dir=Path(), seekdb_path=Path(),
                              state_path=Path(), pc_env_path=Path()), "receiver")
