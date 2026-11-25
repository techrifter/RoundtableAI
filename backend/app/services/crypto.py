import logging
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.config import settings

logger = logging.getLogger("roundtable.crypto")


def _get_key() -> bytes:
    """Returns the parsed AES-256-GCM key from settings.
    Raises RuntimeError if CREDENTIAL_ENCRYPTION_KEY is missing or invalid.
    """
    raw_key = (settings.credential_encryption_key or "").strip()
    if not raw_key:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY environment variable is not configured. "
            "A 32-byte hex-encoded key (64 hex characters) is required to safely encrypt/decrypt stored BYOK credentials."
        )
    try:
        key = bytes.fromhex(raw_key)
    except ValueError:
        raise ValueError("CREDENTIAL_ENCRYPTION_KEY must be a valid hex string")
    
    if len(key) != 32:
        raise ValueError("CREDENTIAL_ENCRYPTION_KEY must be exactly 32 bytes (64 hex characters) for AES-256")
    return key

def encrypt_secret(plaintext: str) -> str:
    """
    Encrypts a secret using AES-256-GCM.
    Returns: a single hex string containing the embedded 12-byte nonce + ciphertext.
    """
    if not plaintext:
        return ""
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode('utf-8'), None)
    return (nonce + ciphertext).hex()

def decrypt_secret(combined_hex: str) -> str:
    """
    Decrypts a combined hex string (nonce + ciphertext) using AES-256-GCM.
    """
    if not combined_hex:
        return ""
    key = _get_key()
    aesgcm = AESGCM(key)
    raw_bytes = bytes.fromhex(combined_hex)
    nonce = raw_bytes[:12]
    ciphertext = raw_bytes[12:]
    plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext_bytes.decode('utf-8')
