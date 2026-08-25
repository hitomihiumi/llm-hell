"""Encrypting the credentials this application is trusted to hold.

Until now the rule was absolute: secrets live in the environment, and the
database records only the *name* of the variable to read, so a dump leaks
nothing. Per-user credentials break that rule, because there is nowhere else
for them to live - a GitLab token belonging to one of twenty people cannot be
an environment variable on a shared container.

So the rule is narrowed rather than abandoned. Deployment-wide secrets still
live in the environment and still never reach a row. What is stored here is
only what a *user* supplied about themselves, it is encrypted with a key that
is itself an environment variable, and a database dump on its own is
therefore still worth nothing.

That last point is the whole design. The key is deliberately not derivable
from anything in the database: an attacker with the dump and without the
environment has ciphertext, and an attacker with both has what they would
have had from `.env` anyway.

Fernet is AES-128-CBC with an HMAC-SHA256 tag and a timestamp, all
authenticated. It is chosen over rolling anything because the failure modes
of hand-built symmetric encryption are silent and this is not the place to
learn them.
"""

import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("llmhell.crypto")


class CredentialEncryptionError(RuntimeError):
    """Raised when a credential cannot be encrypted or read back.

    Deliberately not a subclass of anything the API layer turns into a 500 by
    accident: a missing key is a configuration error the operator has to see,
    and a value that will not decrypt is a credential the user has to supply
    again. Both need saying out loud rather than being swallowed.
    """


def generate_key() -> str:
    """A new key, printable, for `CREDENTIALS_ENCRYPTION_KEY`."""
    return Fernet.generate_key().decode("ascii")


class CredentialCipher:
    """Encrypt and decrypt with the deployment's key.

    Constructed per use rather than held as a module global so that a test
    can hand it a key of its own, and so that a key rotated in the
    environment takes effect on the next request rather than the next
    restart.
    """

    def __init__(self, key: str) -> None:
        if not key:
            raise CredentialEncryptionError(
                "CREDENTIALS_ENCRYPTION_KEY is not set, so per-user credentials cannot be "
                "stored or read. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        try:
            self._fernet = Fernet(key.encode("ascii") if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise CredentialEncryptionError(
                "CREDENTIALS_ENCRYPTION_KEY is not a valid Fernet key: it must be 32 url-safe base64-encoded bytes."
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """The plaintext, or an error naming the likely cause.

        A rotated key is the realistic reason this fails, and the message says
        so: the alternative is an operator reading "invalid token" and
        concluding the user's credential was corrupted.
        """
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise CredentialEncryptionError(
                "a stored credential could not be decrypted - most likely "
                "CREDENTIALS_ENCRYPTION_KEY has changed since it was saved. The user "
                "must supply the credential again."
            ) from exc
