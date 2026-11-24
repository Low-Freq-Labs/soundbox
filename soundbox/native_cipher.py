"""Reuse only SQLCipher's passphrase derivation, never source or row proofs.

SQLCipher supports the identical derived AES key plus its database salt through
its raw-key API. Connections still decrypt/authenticate fresh pages and callers
retain every authority, identity, schema, recovery and transaction check.
https://www.zetetic.net/sqlcipher/sqlcipher-api/#key
"""
from functools import lru_cache
import hashlib


@lru_cache(maxsize=8)
def _derive(passphrase: str, salt: bytes, algorithm: str, iterations: int) -> bytes:
    # Bounded process memory only. Never serialize or log this material.
    return hashlib.pbkdf2_hmac(algorithm, passphrase.encode('utf-8'), salt, iterations, 32)


def key_connection(conn, *, reuse: bool = True) -> None:
    from pyrekordbox.db6.database import BLOB
    from pyrekordbox.utils import deobfuscate

    passphrase = deobfuscate(BLOB)
    conn.execute("PRAGMA key='" + passphrase.replace("'", "''") + "'")
    if not reuse:
        return  # New databases keep SQLCipher's ordinary random-salt creation.
    algorithm = conn.execute('PRAGMA cipher_kdf_algorithm').fetchone()
    iterations = conn.execute('PRAGMA kdf_iter').fetchone()
    salt = conn.execute('PRAGMA cipher_salt').fetchone()
    algorithms = {'PBKDF2_HMAC_SHA512': 'sha512', 'PBKDF2_HMAC_SHA256': 'sha256',
                  'PBKDF2_HMAC_SHA1': 'sha1'}
    if (not algorithm or algorithm[0] not in algorithms or not iterations
            or not salt or type(salt[0]) is not str or len(salt[0]) != 32):
        return
    try:
        count = int(iterations[0])
        raw_salt = bytes.fromhex(salt[0])
    except (ValueError, TypeError, OverflowError):
        return
    if not 1 <= count <= 10_000_000 or len(raw_salt) != 16:
        return
    key = _derive(passphrase, raw_salt, algorithms[algorithm[0]], count)
    # Both values are generated hex, never executable caller input. Explicit salt
    # binds this raw key to the same salt just read by this actual connection.
    conn.execute('PRAGMA key = "x\'' + key.hex() + raw_salt.hex() + '\'"')
