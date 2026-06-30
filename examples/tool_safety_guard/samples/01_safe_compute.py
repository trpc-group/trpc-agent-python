# Sample 01 - SAFE: pure computation and printing, no risky operations.
# Expected decision: allow


def fibonacci(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def main() -> None:
    numbers = [fibonacci(i) for i in range(10)]
    total = sum(numbers)
    print(f"fibonacci sequence: {numbers}")
    print(f"sum: {total}")


if __name__ == "__main__":
    main()
