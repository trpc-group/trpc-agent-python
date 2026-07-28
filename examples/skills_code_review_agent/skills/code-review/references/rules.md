# Rules

- Flag subprocess and shell execution that enables a shell or embeds dynamic input.
- Flag hard-coded credentials and tokens.
- Flag blocking file or database resources opened without an obvious context manager.
- Flag created asyncio tasks that are neither retained nor awaited.
- Flag production-source changes without an accompanying test-file change.

Rules intentionally prefer high precision. Uncertain results belong in the
human-review section.
