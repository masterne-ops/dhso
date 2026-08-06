from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def quote_env(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy the existing AI code-agent provider into resolver.env"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads(args.source.read_text(encoding="utf-8"))
    base_url = str(config.get("base_url") or "").strip()
    auth_token = str(config.get("auth_token") or "").strip()
    model = str(config.get("model") or "").strip()
    small_model = str(config.get("small_model") or model).strip()
    if not base_url or not auth_token or not model:
        raise ValueError("source config requires base_url, auth_token, and model")

    values = {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_AUTH_TOKEN": auth_token,
        "ANTHROPIC_API_KEY": auth_token,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": small_model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "API_TIMEOUT_MS": "300000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(f"{key}={quote_env(value)}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    os.chmod(args.output, 0o640)
    print(json.dumps({
        "configured": True,
        "provider": base_url.split("/")[2] if "://" in base_url else "custom",
        "model": model,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
