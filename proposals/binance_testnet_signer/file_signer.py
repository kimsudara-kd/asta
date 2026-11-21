"""Ed25519 signer backed by a private key FILE (step E, testnet only; no-VM design: the file belongs to the credential user).

Refuses a key file that is not owned by this user (or root) or is readable/writable by anyone else. There is no accessor
for the key, it cannot be pickled, ``repr`` shows only a short fingerprint of the PUBLIC key. A key vault can replace this
class later by implementing the same ``Signer`` interface.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from signer import Signer, SignerError, SignerUnavailable


class FileEd25519Signer(Signer):
    synthetic = False

    def __init__(self, path: str) -> None:
        st = os.stat(path)
        if stat.S_IMODE(st.st_mode) & 0o177:  # only owner-read (0400) or owner read/write (0600)
            raise SignerError("private key file must be mode 0400 or 0600")
        if st.st_uid not in (os.getuid(), 0):
            raise SignerError("private key file must be owned by this user")
        try:
            key = serialization.load_pem_private_key(open(path, "rb").read(), password=None)
        except (ValueError, TypeError) as exc:
            raise SignerError("not a valid unencrypted PEM private key") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise SignerError("not an Ed25519 key")
        self.__key = key
        raw = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self._pub = base64.b64encode(raw).decode()
        self._fp = hashlib.sha256(self._pub.encode()).hexdigest()[:12]
        self.available = True

    def sign(self, message: bytes) -> bytes:
        if not self.available:
            raise SignerUnavailable("signing service unavailable")
        return self.__key.sign(message)

    def public_key_b64(self) -> str:
        return self._pub

    def __repr__(self) -> str:
        return f"<FileEd25519Signer key={self._fp}>"

    def __getstate__(self) -> None:
        raise SignerError("a signer is not serialisable")
