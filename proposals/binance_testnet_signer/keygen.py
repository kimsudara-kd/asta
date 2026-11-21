"""Generate the Ed25519 key pair for the Binance Spot TESTNET (step E). Run by the owner, as the user that will hold the key.

    python keygen.py --private /path/to/testnet-ed25519.pem

* The PRIVATE key is written to the given path with mode 0400, atomically, and the file must not already exist. It is never
  printed, never logged, never sent anywhere. Nobody else (including the assistant) ever needs to see it.
* The PUBLIC key (PEM) is printed: paste it into the testnet website's API-key page ("Ed25519 public key"). The website then
  shows an API key id; store that in a second private file (0400) for the probe/service. Revoke by deleting the key on the site.
"""

from __future__ import annotations

import hashlib
import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate(private_path: str) -> str:
    """Returns the public key PEM. Raises FileExistsError rather than ever overwriting a key."""
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        os.write(fd, pem)
        os.fsync(fd)
    finally:
        os.close(fd)
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "--private":
        print("usage: keygen.py --private FILE", file=sys.stderr)
        return 2
    try:
        pub = generate(argv[2])
    except FileExistsError:
        print("REFUSING: that file already exists (a key is never overwritten)", file=sys.stderr)
        return 1
    print(pub, end="")
    print(
        f"public key fingerprint: {hashlib.sha256(pub.encode()).hexdigest()[:16]}", file=sys.stderr
    )
    print(f"private key written to {argv[2]} (mode 0400). It was NOT printed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
