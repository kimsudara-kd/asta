#!/usr/bin/env python3
"""Render the OPTIONAL pf anchor that confines the credential user (no-VM design). It only PRINTS text; it loads nothing.

    python3 scripts/render_pf_anchor.py --testnet-ips A,B --dns C,D > asta.pf

Rules: user ``asta-cred`` may open TCP/443 to the given testnet addresses and DNS to the given resolvers, and nothing else; the
rule set never affects any other user (the testnet addresses sit behind a shared CDN, so blocking them for everyone would break
unrelated sites). Loading it is a privileged, shared-firewall change that the owner does by hand (docs/m3b/NO-VM-DEPLOYMENT.md).
Only public unicast IPv4 addresses are accepted; anything else exits 1 with REFUSING.
"""

from __future__ import annotations

import ipaddress
import sys

USER = "asta-cred"


def public_ipv4(text: str) -> list[str]:
    out = []
    for raw in text.split(","):
        raw = raw.strip()
        try:
            ip = ipaddress.IPv4Address(raw)
        except ValueError as exc:
            raise ValueError(f"not an IPv4 address: {raw!r}") from exc
        if (
            not ip.is_global
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_loopback
            or ip.is_link_local
        ):
            raise ValueError(f"not a public unicast address: {raw}")
        if ipaddress.IPv4Address("100.64.0.0") <= ip <= ipaddress.IPv4Address("100.127.255.255"):
            raise ValueError(f"carrier-grade NAT range: {raw}")
        out.append(str(ip))
    if not out or len(out) > 32 or len(set(out)) != len(out):
        raise ValueError("give 1..32 distinct addresses")
    return out


def render(testnet: list[str], dns: list[str]) -> str:
    return "\n".join(
        [
            "# ASTA optional anchor: confines the credential-service user. Generated; review before loading.",
            f"table <asta_testnet> persist {{ {' '.join(testnet)} }}",
            f"table <asta_dns> persist {{ {' '.join(dns)} }}",
            f"pass out quick proto tcp from any to <asta_testnet> port 443 user {USER} keep state",
            f"pass out quick proto {{ udp tcp }} from any to <asta_dns> port 53 user {USER} keep state",
            f"block drop out quick user {USER}",
            "",
        ]
    )


def main(argv: list[str]) -> int:
    try:
        opts = dict(zip(argv[1::2], argv[2::2], strict=False))
        if set(opts) != {"--testnet-ips", "--dns"} or len(argv) != 5:
            raise ValueError("usage: render_pf_anchor.py --testnet-ips A,B --dns C,D")
        sys.stdout.write(render(public_ipv4(opts["--testnet-ips"]), public_ipv4(opts["--dns"])))
        return 0
    except ValueError as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
