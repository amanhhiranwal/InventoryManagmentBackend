import secrets

from fastapi import HTTPException
from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()

#: The shortest password anybody may choose for themselves.
MIN_LENGTH = 8

#: Symbols that count as "a symbol". Everything that is not a letter, a
#: digit, a space or an equals sign, written out rather than inferred so
#: the rule on screen and the rule here are the same list.
SYMBOLS = "!@#$%^&*()-_+[]{};:,.<>?/\\|~`'\""


class PasswordService:

    @staticmethod
    def hash_password(password: str) -> str:
        return password_hash.hash(password)

    @staticmethod
    def verify_password(password: str, hashed_password: str) -> bool:
        return password_hash.verify(password, hashed_password)

    @staticmethod
    def problems(password: str) -> list[str]:
        """Everything wrong with a password somebody is choosing, in order.

        All of them at once, rather than the first: being told "too short",
        then "needs a capital", then "needs a digit" over three attempts is
        the same refusal three times.

        A space and an equals sign are refused outright. Neither belongs in
        a password anyone will type twice, and keeping them out means the
        value never has to be reasoned about where passwords are pasted
        into something else.
        """

        value = password or ""

        found: list[str] = []

        if len(value) < MIN_LENGTH:
            found.append(f"at least {MIN_LENGTH} characters")

        if not any(c.islower() for c in value):
            found.append("a small letter")

        if not any(c.isupper() for c in value):
            found.append("a capital letter")

        if not any(c.isdigit() for c in value):
            found.append("a number")

        if not any(c in SYMBOLS for c in value):
            found.append("a symbol such as ! @ # $")

        problems = []

        if found:
            problems.append("needs " + ", ".join(found))

        if any(c.isspace() for c in value):
            problems.append("cannot contain a space")

        if "=" in value:
            problems.append("cannot contain an equals sign")

        return problems

    @staticmethod
    def assert_strong(password: str) -> None:
        """Refuse a password that does not meet the rules, saying why."""

        problems = PasswordService.problems(password)

        if problems:
            raise HTTPException(
                status_code=400,
                detail="A password " + " and ".join(problems) + ".",
            )

    @staticmethod
    def temporary_password() -> str:
        """A password to email somebody who has not got one yet.

        Read aloud or retyped from a phone as often as it is pasted, so
        the alphabet leaves out the characters people confuse - no O or
        0, no l or 1 - and the shape is fixed: letters, digits, one
        symbol. It is strong enough to be worth nothing to a guesser and
        short-lived either way, because the account it belongs to cannot
        do anything until the password has been changed.

        It meets the rules a person's own password has to meet - one of
        each kind, and nothing that has to be typed with a shift and a
        guess - so nobody is ever sent something the next screen would
        refuse.
        """

        upper = "ABCDEFGHJKMNPQRSTUVWXYZ"
        lower = "abcdefghjkmnpqrstuvwxyz"
        digits = "23456789"
        symbols = "!@#$%&*"

        # One of each first, so the shape is guaranteed rather than likely,
        # then shuffled so the symbol is not always last.
        picked = [
            secrets.choice(upper),
            secrets.choice(lower),
            secrets.choice(digits),
            secrets.choice(symbols),
        ]

        picked += [
            secrets.choice(upper + lower + digits) for _ in range(7)
        ]

        secrets.SystemRandom().shuffle(picked)

        return "".join(picked)
