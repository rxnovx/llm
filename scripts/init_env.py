"""Create .env with independent random secrets, without printing them."""
import argparse
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--compose", action="store_true", help="Use http://localhost:8080 for the bundled Nginx")
parser.add_argument("--postgres", action="store_true", help="Also generate a PostgreSQL password")
args = parser.parse_args()
target = root / ".env"
text = (root / ".env.example").read_text()
values = {"JWT_SECRET_KEY": secrets.token_hex(32), "AUTH_PROXY_SECRET": secrets.token_hex(32)}
if args.compose:
    values.update(APP_URL="http://localhost:8080", FRONTEND_URL="http://localhost:8080")
if args.postgres:
    values["POSTGRES_PASSWORD"] = secrets.token_hex(32)
text = "\n".join(f"{line.split('=', 1)[0]}={values[line.split('=', 1)[0]]}"
                 if '=' in line and line.split('=', 1)[0] in values else line for line in text.splitlines()) + "\n"
try:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print(".env already exists; existing settings were preserved.")
else:
    with os.fdopen(descriptor, "w") as output:
        output.write(text)
    print("Created .env. Random secrets were saved without being displayed.")
