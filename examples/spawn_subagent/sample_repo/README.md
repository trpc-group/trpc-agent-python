# shop — tiny sample repo

A miniature Python project shared by the `dynamic_subagent` examples (`basic/`
and `with_md/`). Sub-agents explore and discuss this small codebase instead of the real `trpc-agent-python`
repo, so the demo runs fast and produces predictable output.

Modules:
- `app.py`: HTTP-style request handlers (login / checkout / refund).
- `auth.py`: token verification helpers.
- `cart.py`: cart math.
- `db.py`: in-memory store stub.

There is exactly one TODO comment in this repo (in `cart.py`), so the
"summarize TODOs" demo query has a deterministic answer.
