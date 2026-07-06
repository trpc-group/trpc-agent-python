from pathlib import Path


def main() -> None:
    output = Path("out/safe_report.txt")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("quickstart report generated\n", encoding="utf-8")
    print("report ready")


if __name__ == "__main__":
    main()
