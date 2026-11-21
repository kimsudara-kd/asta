"""The ONLY network code of the credential side (step E): an HTTPS transport pinned to the Binance Spot Testnet host.

* https only; the host must be exactly ``testnet.binance.vision`` (a loopback test base is possible only when explicitly allowed).
* no redirects, no proxies picked up from the environment, certificate and host-name verification on, hard timeouts,
  response size cap, JSON only. Transport trouble raises TimeoutError/OSError (the connector maps those to UNKNOWN).
* it never logs or returns a URL (a signed URL carries the signature) and never sees the private key: requests arrive signed.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from signer import TESTNET_BASE, SignedRequest

TESTNET_HOST = urlsplit(TESTNET_BASE).hostname
MAX_BYTES = 65_536
PUBLIC_PATHS = frozenset({"/api/v3/time", "/api/v3/exchangeInfo"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:
        return None


class HttpsTransport:
    def __init__(
        self,
        context: ssl.SSLContext | None = None,
        timeout_s: float = 10.0,
        test_base: str | None = None,
    ) -> None:
        if test_base is not None:
            p = urlsplit(test_base)
            if p.scheme != "https" or p.hostname not in ("127.0.0.1", "localhost"):
                raise ValueError("a test base must be https on loopback")
        self._test_base = test_base
        ctx = context or ssl.create_default_context()
        if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
            raise ValueError("certificate and host-name verification must stay on")
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect, urllib.request.HTTPSHandler(context=ctx)
        )
        self._timeout = timeout_s

    def _target(self, url: str) -> str:
        p = urlsplit(url)
        if p.scheme != "https" or p.hostname != TESTNET_HOST or p.port not in (None, 443):
            raise ValueError("only the Binance Spot Testnet host is reachable")
        if self._test_base is None:
            return url
        t = urlsplit(self._test_base)
        return urlunsplit((t.scheme, t.netloc, p.path, p.query, ""))

    def _do(self, req: urllib.request.Request) -> tuple[int, dict[str, Any]]:
        try:
            with self._opener.open(req, timeout=self._timeout) as r:  # noqa: S310 - scheme/host pinned above
                status, raw = r.status, r.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read(MAX_BYTES + 1)
        except (urllib.error.URLError, ssl.SSLError, ConnectionError) as exc:
            raise OSError(type(exc).__name__) from None  # never carries the URL
        if len(raw) > MAX_BYTES:
            raise OSError("response too large")
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise OSError("response is not JSON") from None
        return status, body if isinstance(body, dict) else {"value": body}

    def __call__(self, request: SignedRequest, timeout_s: float) -> tuple[int, dict[str, Any]]:
        req = urllib.request.Request(  # noqa: S310
            self._target(request.url), method=request.method, headers=dict(request.headers)
        )
        return self._do(req)

    def public_get(self, path: str) -> tuple[int, dict[str, Any]]:
        if path not in PUBLIC_PATHS:
            raise ValueError("not a permitted public path")
        req = urllib.request.Request(self._target(f"{TESTNET_BASE}{path}"), method="GET")  # noqa: S310
        return self._do(req)
