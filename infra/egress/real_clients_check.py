"""(M3A-MAC-01) Runs ASTA's REAL outbound clients (PublicHttp over httpx, and the websocket stream helper) from inside the
protected network, using ONLY the proxy via environment variables. Public unauthenticated data only."""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/src/src")
from asta.exchanges.common import ws as wsmod  # noqa: E402
from asta.exchanges.common.http import PublicHttp  # noqa: E402

out = {"HTTPS_PROXY": os.environ.get("HTTPS_PROXY")}


async def main() -> None:
    http = PublicHttp("https://data-api.binance.vision")
    try:
        t = await http.get("/api/v3/time")
        out["rest_time_via_proxy"] = "OK" if "serverTime" in t else str(t)[:80]
    except Exception as exc:  # noqa: BLE001
        out["rest_time_via_proxy"] = f"ERROR {type(exc).__name__}: {exc}"[:200]
    finally:
        await http.aclose()
    try:
        n = 0
        async for m in wsmod.ws_messages(
            "wss://data-stream.binance.vision/ws/btcusdt@trade", max_attempts=0
        ):
            if isinstance(m, str):
                out["ws_first_message_via_proxy"] = (
                    "OK" if "btcusdt" in m.lower() or '"p"' in m else m[:80]
                )
                break
            n += 1
            if n > 3:
                break
    except Exception as exc:  # noqa: BLE001
        out["ws_first_message_via_proxy"] = f"ERROR {type(exc).__name__}: {exc}"[:200]
    # control: with the proxy variables removed the same client must FAIL (no route out)
    os.environ.pop("HTTPS_PROXY", None)
    os.environ.pop("https_proxy", None)
    http2 = PublicHttp("https://data-api.binance.vision")
    try:
        await http2.get("/api/v3/time")
        out["control_without_proxy"] = "UNEXPECTEDLY CONNECTED"
    except Exception as exc:  # noqa: BLE001
        out["control_without_proxy"] = f"blocked ({type(exc).__name__})"
    finally:
        await http2.aclose()


asyncio.run(main())
print(json.dumps(out, indent=1))
