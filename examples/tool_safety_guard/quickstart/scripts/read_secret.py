from pathlib import Path


def main() -> None:
    token = Path(".env").read_text(encoding="utf-8")
    print(token)


if __name__ == "__main__":
    main()
