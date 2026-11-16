"""Real local Core lifecycle check. --live-model adds exactly one short inference call.

No app launch, Crew execution, Firebase login or Google account operation. Only a
service started by this check is stopped afterwards; adopted services remain alone.
Credentials stay in memory; only non-sensitive diagnostics are printed.
"""

import argparse
import asyncio
import time

import httpx
from mavo.credentials import DpapiCredentialStore
from mavo.dev_core import DevCore
from mavo.publisher_gateway import GatewaySettings


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-model", action="store_true")
    args = parser.parse_args()
    manager = DevCore("http://127.0.0.1:8741", True)
    manager.start()
    try:
        deadline = time.monotonic() + 25
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            while time.monotonic() < deadline:
                if await manager.probe():
                    break
                await asyncio.sleep(0.5)
            else:
                print(
                    {
                        "service": "failed",
                        "state": manager.state,
                        "log": str(manager.log_path),
                    }
                )
                raise SystemExit(1)
            health = (await client.get("http://127.0.0.1:8741/health")).json()
        print(
            {
                "service": "ready",
                "configured": health["configured"],
                "owned_test_process": manager.child is not None,
                "model": health["model"],
            },
            flush=True,
        )
        if not args.live_model:
            return
        if not health["configured"]:
            print({"model_test": "not_run", "reason": "Publisher key is missing."})
            raise SystemExit(1)
        settings = GatewaySettings()
        secret = (
            settings.api_key.get_secret_value()
            if settings.api_key
            else DpapiCredentialStore(settings.data_dir).get("publisher-airouter")
        )
        if not secret:
            raise SystemExit("Publisher key is unavailable.")
        print("AIRouter connectivity request 1/1; no tool execution.", flush=True)
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
            response = await client.post(
                "https://api.airouter.in/v1/chat/completions",
                headers={"Authorization": "Bearer " + secret},
                json={
                    "model": settings.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Reply with exactly MAVO_CORE_OK. No tools.",
                        }
                    ],
                    "temperature": 0,
                    "max_tokens": 128,
                    "reasoning_effort": settings.reasoning_effort,
                },
            )
        if response.status_code != 200:
            print(
                {
                    "model_test": "failed",
                    "provider_http_status": response.status_code,
                    "calls": 1,
                }
            )
            raise SystemExit(1)
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        matched = isinstance(content, str) and content.strip() == "MAVO_CORE_OK"
        print(
            {
                "model_test": "passed" if matched else "unexpected_reply",
                "calls": 1,
                "reported_total_tokens": data.get("usage", {}).get("total_tokens"),
                "crew_activated": False,
                "account_actions": 0,
            },
            flush=True,
        )
        if not matched:
            raise SystemExit(1)
    finally:
        await manager.close()
        print(
            "Test-owned Core process cleaned up; existing services were not stopped.",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
