"""Create shared or standalone environment files without overwriting existing files."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets

def create_env(root: Path, template: str, destination: str, values: dict[str, str]) -> None:
    target = root / destination
    lines = (root / template).read_text().splitlines()
    text = "\n".join(f"{key}={values[key]}" if (key := line.split('=', 1)[0]) in values else line
                     for line in lines) + "\n"
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"{destination} already exists; existing settings were preserved.")
    else:
        with os.fdopen(descriptor, "w") as output:
            output.write(text)
        print(f"Created {destination}. Random secrets were saved without being displayed.")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--compose", action="store_true", help="Use http://localhost:8080 for the bundled Nginx")
    mode.add_argument("--standalone", action="store_true", help="Create backend/.env and frontend/.env.local for separate processes")
    parser.add_argument("--postgres", action="store_true", help="Also generate a PostgreSQL password in the shared .env")
    args = parser.parse_args()
    if args.standalone and args.postgres:
        parser.error("--postgres is for shared .env setup; configure DATABASE_URL in backend/.env for standalone PostgreSQL")
    values = {"JWT_SECRET_KEY": secrets.token_hex(32), "AUTH_PROXY_SECRET": secrets.token_hex(32),
              "INTEGRATIONS_ENCRYPTION_KEYS": json.dumps([base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()])}
    if args.standalone:
        create_env(root, "backend/.env.example", "backend/.env", values)
        create_env(root, "frontend/.env.example", "frontend/.env.local", values)
        return
    if args.compose:
        values.update(APP_URL="http://localhost:8080", APP_ALLOWED_ORIGINS="", FRONTEND_URL="http://localhost:8080")
    if args.postgres:
        values["POSTGRES_PASSWORD"] = secrets.token_hex(32)
    create_env(root, ".env.example", ".env", values)


if __name__ == "__main__":
    main()
