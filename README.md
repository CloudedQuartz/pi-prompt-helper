# pi-prompt-helper

A small local Pi extension that removes low-information prompt text before a
message is sent. The goal is simple: keep typing responsive, fail open, and make
only deletion-only edits that preserve meaning-critical text.

## Behavior

- Registers immediately; it does not start Python during extension startup.
- The first eligible prompt starts a background cleaner health check and is sent
  unchanged.
- Prompts are cleaned only after the cleaner is ready.
- If the cleaner is loading, unavailable, slow, or returns invalid output, the
  extension returns `continue` and leaves the prompt unchanged.
- Cleaning is local and deletion-only. It never paraphrases or reorders text.
- Slash commands, extension-originated messages, blank prompts, and image prompts
  are skipped by default.

## Commands

- `/exact <prompt>` sends text exactly as written and bypasses cleanup.
- `/prompt-cleaner-toggle` toggles cleanup for the current process.
- `/prompt-cleaner-status` shows readiness.
- `/prompt-cleaner-stats` shows process-local counters.

## Configuration

Environment variables:

- `PI_PROMPT_HELPER_ENABLED=0` disables cleanup by default.
- `PI_PROMPT_HELPER_PYTHON=/path/to/python` selects the Python executable.
- `PI_PROMPT_HELPER_CLEANER=/path/to/prompt_cleaner.py` selects the cleaner.
- `PI_PROMPT_HELPER_MIN_SAVINGS=0.03` sets the minimum deletion ratio.
- `PI_PROMPT_HELPER_TIMEOUT_MS=2000` limits each cleaner process.
- `PI_PROMPT_HELPER_CLEAN_SLASH=1` allows cleaning slash-prefixed input.
- `PI_PROMPT_HELPER_CLEAN_IMAGES=1` allows cleaning prompts with images.

## Development

```sh
npm install
npm run check
npm test
npm run smoke
```

Manual cleaner usage:

```sh
echo 'Please, um, basically summarize this very small note.' \
  | python3 python/prompt_cleaner.py --min-savings 0 --pretty
```
