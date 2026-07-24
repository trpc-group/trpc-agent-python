"""Sample 01 — Safe Python script (pure computation).

Expected decision: ALLOW
This script performs basic arithmetic and string operations with no risky behavior.
"""


def fibonacci(n: int) -> list[int]:
    """Compute the first n Fibonacci numbers."""
    if n <= 0:
        return []
    if n == 1:
        return [0]
    seq = [0, 1]
    for _ in range(2, n):
        seq.append(seq[-1] + seq[-2])
    return seq


def is_prime(num: int) -> bool:
    """Check if a number is prime."""
    if num < 2:
        return False
    for i in range(2, int(num**0.5) + 1):
        if num % i == 0:
            return False
    return True


if __name__ == "__main__":
    fib = fibonacci(20)
    primes = [x for x in fib if is_prime(x)]
    print(f"Fibonacci(20): {fib}")
    print(f"Primes in Fibonacci: {primes}")
    print(f"Sum: {sum(fib)}")
