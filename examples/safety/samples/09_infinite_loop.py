"""Sample 09 — Infinite loop without termination condition.

Expected decision: NEEDS_HUMAN_REVIEW
Triggers: RES-001 (infinite loop — while True without break)
"""

import time


def spin_forever():
    """Spin in an infinite loop consuming CPU — no exit condition!"""
    counter = 0
    while True:
        counter += 1
        time.sleep(0.001)
        # No break, no return — this never terminates


if __name__ == "__main__":
    spin_forever()
