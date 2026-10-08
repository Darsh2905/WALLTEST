"""Ed25519 signing of frozen verdict snapshots (migration 014). The private key lives OUTSIDE the database (a file readable
only by the engine; a docker volume in the compose stack), so a database superuser who rewrites audit rows cannot re-sign them.
The public key is published with every snapshot and the browser verifies the signature with Web Crypto."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import config


class Signer:
    def __init__(self, path: Path):
        self.path = path
        pem = b"" if path.exists() else Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        self._key = serialization.load_pem_private_key(config.publish_secret_once(path, pem), password=None)
        if not isinstance(self._key, Ed25519PrivateKey):
            raise ValueError(f"{path} is not an Ed25519 private key")

    @property
    def public_hex(self) -> str:
        return self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def sign(self, message: bytes) -> str:
        return self._key.sign(message).hex()


def verify(public_hex: str, message: bytes, signature_hex: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex)).verify(bytes.fromhex(signature_hex), message)
        return True
    except (InvalidSignature, ValueError):
        return False


@lru_cache(maxsize=1)
def signer() -> Signer:
    return Signer(config.SIGNING_KEY_PATH)
