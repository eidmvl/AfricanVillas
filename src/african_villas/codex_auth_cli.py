from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any


def _account_payload(response: Any) -> dict[str, Any]:
    if response.account is None:
        return {"authenticated": False}
    account = response.account.root
    plan = getattr(account, "plan_type", None)
    return {
        "authenticated": True,
        "type": getattr(account, "type", "unknown"),
        "plan": getattr(plan, "value", plan),
    }


async def _status(*, refresh: bool) -> dict[str, Any]:
    from openai_codex import AsyncCodex

    async with AsyncCodex() as codex:
        return _account_payload(await codex.account(refresh_token=refresh))


async def _device_login() -> dict[str, Any]:
    from openai_codex import AsyncCodex

    async with AsyncCodex() as codex:
        existing = _account_payload(await codex.account(refresh_token=True))
        if existing.get("authenticated") and existing.get("type") == "chatgpt":
            print("Codex is already authenticated with ChatGPT.", flush=True)
            return existing

        handle = await codex.login_chatgpt_device_code()
        print("Open this address in your browser:", flush=True)
        print(handle.verification_url, flush=True)
        print("Enter this one-time code:", flush=True)
        print(handle.user_code, flush=True)
        print("Waiting for confirmation...", flush=True)
        result = await handle.wait()
        if not result.success:
            raise RuntimeError(result.error or "ChatGPT device login failed")
        return _account_payload(await codex.account(refresh_token=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the dedicated Codex login cache")
    parser.add_argument("command", choices=("login", "status"))
    args = parser.parse_args()
    if args.command == "login":
        payload = asyncio.run(_device_login())
    else:
        payload = asyncio.run(_status(refresh=True))
    print(json.dumps(payload, ensure_ascii=False))
    if not payload.get("authenticated"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
