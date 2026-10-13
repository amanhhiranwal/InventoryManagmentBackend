import secrets

from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()


class PasswordService:

    @staticmethod
    def hash_password(password: str) -> str:
        return password_hash.hash(password)

    @staticmethod
    def verify_password(password: str, hashed_password: str) -> bool:
        return password_hash.verify(password, hashed_password)

    @staticmethod
    def temporary_password() -> str:
        """A password to email somebody who has not got one yet.

        Read aloud or retyped from a phone as often as it is pasted, so
        the alphabet leaves out the characters people confuse - no O or
        0, no l or 1 - and the shape is fixed: letters, digits, one
        symbol. It is strong enough to be worth nothing to a guesser and
        short-lived either way, because the account it belongs to cannot
        do anything until the password has been changed.
        """

        letters = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz"
        digits = "23456789"

        body = "".join(secrets.choice(letters + digits) for _ in range(10))

        return f"{body}{secrets.choice('!@#$%&*')}"