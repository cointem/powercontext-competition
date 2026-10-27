"""Start the official PowerContext server with isolated local game storage."""
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.resolve()


def main():
    binary = shutil.which("powercontext")
    if not binary:
        raise SystemExit('Install first: uv tool install "powercontext[cli,server]==1.4.0"')
    data = ROOT / ".data"
    data.mkdir(exist_ok=True)
    config = data / "runtime.json"
    if not config.exists():
        # Exclusive creation and private permissions from the first byte.
        with os.fdopen(os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as file:
            json.dump({"token": secrets.token_hex(32)}, file)
    token = json.loads(config.read_text())["token"]
    if len(token) < 24:
        raise SystemExit("Invalid local token")
    env = {k: v for k, v in os.environ.items() if not k.startswith("POWERCONTEXT_")}
    env.update(POWERCONTEXT_HOME=str(data / "powercontext"), POWERCONTEXT_SERVER_AUTH_ENABLED="true",
        POWERCONTEXT_SERVER_AUTH_TOKEN=token, POWERCONTEXT_SERVER_HTTP_HOST="127.0.0.1",
        POWERCONTEXT_SERVER_HTTP_PORT="18092", POWERCONTEXT_SERVER_MCP_ENABLED="false")
    print("PowerContext: http://127.0.0.1:18092; isolated data in .data/powercontext", flush=True)
    print("Dify PC_TOKEN: read the token field of .data/runtime.json locally; never commit it.", flush=True)
    subprocess.run([binary, "server", "run", "--no-env-file"], env=env, check=True)


if __name__ == "__main__":
    main()
