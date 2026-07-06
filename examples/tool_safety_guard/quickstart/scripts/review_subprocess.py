import subprocess


def main() -> None:
    subprocess.run(["python", "worker.py"], check=True, timeout=10)


if __name__ == "__main__":
    main()
