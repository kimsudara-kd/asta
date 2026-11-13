#!/bin/sh
# healthy only if default-deny is in force in every hook and the resolver and refresher are running
set -eu
nft list chain inet asta output | grep -q 'policy drop'
nft list chain inet asta input | grep -q 'policy drop'
nft list chain inet asta forward | grep -q 'policy drop'
kill -0 "$(cat /run/pid-dnsmasq)"
kill -0 "$(cat /run/pid-refresh)"
