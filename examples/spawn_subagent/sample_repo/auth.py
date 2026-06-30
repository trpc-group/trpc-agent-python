"""Auth helpers for the sample shop app."""

VALID_TOKENS = {"alice-token", "bob-token"}


def verify_token(token: str) -> bool:
    return token in VALID_TOKENS


def user_of(token: str) -> str:
    if token == "alice-token":
        return "alice"
    if token == "bob-token":
        return "bob"
    raise ValueError(f"unknown token: {token}")
