# Sample 09 - RESOURCE ABUSE: an unbounded loop with no termination condition.
# Expected decision: needs_human_review  (RES_INFINITE_LOOP, MEDIUM)

counter = 0
while True:
    counter += 1
