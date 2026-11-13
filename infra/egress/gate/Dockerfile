# Egress gate: kernel (netfilter) enforcement + allowlist-only DNS, in the network namespace that the
# proxy shares. Small, pinned base; no application code. See docs/m3a/EGRESS-ENFORCEMENT.md.
FROM alpine:3.20
RUN apk add --no-cache nftables dnsmasq jq \
    && rm -rf /var/cache/apk/*
COPY entrypoint.sh /usr/local/bin/gate-entrypoint.sh
COPY healthcheck.sh /usr/local/bin/gate-healthcheck.sh
RUN chmod 0555 /usr/local/bin/gate-entrypoint.sh /usr/local/bin/gate-healthcheck.sh
ENTRYPOINT ["/usr/local/bin/gate-entrypoint.sh"]
