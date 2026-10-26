"""Create local service credentials without putting them in the submitted DSL."""
from pathlib import Path
import secrets

path = Path(__file__).parent / ".env"
if path.exists():
    print(".env already exists; preserving it")
else:
    path.write_text("GAME_TOKEN=" + secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    path.chmod(0o600)
    print("Created .env; copy GAME_TOKEN locally into the Dify workflow environment variable")
