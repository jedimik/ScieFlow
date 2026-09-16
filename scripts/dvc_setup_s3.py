#!/usr/bin/env python3
"""CLI helper to configure DVC S3 remote for ScieFlow.

Supports:
- Reading options directly from `.env` file (or specified env file)
- CLI argument overrides
- Storing credentials safely in `.dvc/config.local` (ignored by Git)
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from scieflow.core import config


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse simple .env key=value file, ignoring comments and blanks."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and val:
                env[key] = val
    return env


def normalize_s3_url(url: str) -> tuple[str, str | None]:
    """Validate and normalize S3 URL and optional endpoint.

    Handles cases like:
      - s3://bucket/prefix -> ('s3://bucket/prefix', None)
      - https://endpoint/bucket/prefix -> ('s3://bucket/prefix', 'https://endpoint')
      - host://bucket/prefix -> ('s3://bucket/prefix', 'https://host')
    """
    url = url.strip()
    detected_endpoint = None

    if url.startswith("s3://"):
        return url, None

    # Handle accidental host://bucket/prefix or https://host/bucket/prefix
    if "://" in url:
        scheme, rest = url.split("://", 1)
        if scheme in ("http", "https"):
            parts = rest.split("/", 1)
            host = parts[0]
            path = parts[1] if len(parts) > 1 else ""
            detected_endpoint = f"{scheme}://{host}"
            url = f"s3://{path}"
        else:
            # e.g., s3.cl4.du.cesnet.cz://dvc-projects/scieflow
            detected_endpoint = f"https://{scheme}"
            url = f"s3://{rest.lstrip('/')}"
    else:
        # e.g. without scheme: bucket/prefix
        url = f"s3://{url.lstrip('/')}"

    return url, detected_endpoint


def run_cmd(cmd: list[str], root: Path) -> None:
    res = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error running {' '.join(cmd)}:\n{res.stderr.strip()}", file=sys.stderr)
        sys.exit(res.returncode)


def setup_s3(
    root: Path,
    url: str,
    remote_name: str = "s3remote",
    endpoint_url: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> None:
    norm_url, detected_endpoint = normalize_s3_url(url)
    final_endpoint = endpoint_url or detected_endpoint

    print(f"Adding DVC remote '{remote_name}' with URL '{norm_url}'...")
    run_cmd(["dvc", "remote", "add", "-d", "-f", remote_name, norm_url], root)

    if final_endpoint:
        print(f"Setting endpointurl: {final_endpoint}")
        run_cmd(["dvc", "remote", "modify", remote_name, "endpointurl", final_endpoint], root)

    if region:
        print(f"Setting region: {region}")
        run_cmd(["dvc", "remote", "modify", remote_name, "region", region], root)

    if profile:
        print(f"Setting profile: {profile}")
        run_cmd(["dvc", "remote", "modify", remote_name, "profile", profile], root)

    # Store credentials ONLY in local config (.dvc/config.local, which is gitignored)
    if access_key_id:
        print("Storing access_key_id in local gitignored config (.dvc/config.local)...")
        run_cmd(["dvc", "remote", "modify", "--local", remote_name, "access_key_id", access_key_id], root)

    if secret_access_key:
        print("Storing secret_access_key in local gitignored config (.dvc/config.local)...")
        run_cmd(["dvc", "remote", "modify", "--local", remote_name, "secret_access_key", secret_access_key], root)

    print(f"\nDVC remote '{remote_name}' successfully configured!")
    print(f"  Remote URL:    {norm_url}")
    if final_endpoint:
        print(f"  Endpoint URL:  {final_endpoint}")
    if region:
        print(f"  Region:        {region}")
    if access_key_id:
        print("  Credentials:   Stored in .dvc/config.local (gitignored)")


def main() -> None:
    root = config.repo_root()
    default_env_path = root / ".env"

    ap = argparse.ArgumentParser(description="Configure DVC S3 remote for ScieFlow")
    ap.add_argument("--env-file", type=Path, default=default_env_path, help="Path to .env file (default: repo root .env)")
    ap.add_argument("--url", help="S3 URL (e.g., s3://bucket/path or s3.endpoint://bucket/path)")
    ap.add_argument("--remote-name", help="Remote name (default: s3remote)")
    ap.add_argument("--endpoint-url", help="Custom S3 endpoint URL (e.g., https://s3.cl4.du.cesnet.cz)")
    ap.add_argument("--region", help="AWS / S3 region (e.g., eu-central-1)")
    ap.add_argument("--profile", help="AWS CLI credential profile name to use")
    ap.add_argument("--access-key-id", help="AWS Access Key ID (stored in gitignored .dvc/config.local)")
    ap.add_argument("--secret-access-key", help="AWS Secret Access Key (stored in gitignored .dvc/config.local)")

    args = ap.parse_args()

    # Load from .env if present
    env_vars = {}
    if args.env_file.exists():
        print(f"Loading defaults from {args.env_file.name}...")
        env_vars = parse_env_file(args.env_file)

    # Resolution priority: CLI argument > .env file > os.environ > fallback default
    url = args.url or env_vars.get("DVC_S3_URL") or os.environ.get("DVC_S3_URL")
    if not url:
        print("Error: S3 URL must be provided via --url, DVC_S3_URL in .env, or environment variable.", file=sys.stderr)
        sys.exit(1)

    remote_name = (
        args.remote_name
        or env_vars.get("DVC_S3_REMOTE_NAME")
        or os.environ.get("DVC_S3_REMOTE_NAME")
        or "s3remote"
    )
    endpoint_url = (
        args.endpoint_url
        or env_vars.get("DVC_S3_ENDPOINT_URL")
        or os.environ.get("DVC_S3_ENDPOINT_URL")
    )
    region = (
        args.region
        or env_vars.get("DVC_S3_REGION")
        or os.environ.get("DVC_S3_REGION")
        or env_vars.get("AWS_DEFAULT_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
    )
    profile = (
        args.profile
        or env_vars.get("AWS_PROFILE")
        or os.environ.get("AWS_PROFILE")
    )
    access_key_id = (
        args.access_key_id
        or env_vars.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("AWS_ACCESS_KEY_ID")
    )
    secret_access_key = (
        args.secret_access_key
        or env_vars.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("AWS_SECRET_ACCESS_KEY")
    )

    setup_s3(
        root=root,
        url=url,
        remote_name=remote_name,
        endpoint_url=endpoint_url,
        region=region,
        profile=profile,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )


if __name__ == "__main__":
    main()
