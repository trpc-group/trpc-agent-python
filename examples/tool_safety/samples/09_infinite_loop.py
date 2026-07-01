# Sample 09: Infinite loop / resource abuse.
# Should be DENIED (resource_abuse, HIGH).
while True:
    print("spinning forever")

i = 0
while True:
    i += 1

# fork bomb equivalent in Python via multiprocessing
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=10000) as pool:
    pass

import time
time.sleep(99999)
