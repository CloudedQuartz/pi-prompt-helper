# pi-prompt-helper

Local, deletion-only prompt compression for the pi coding agent. The extension intercepts raw `input` events, calls a local Python sidecar, and only transforms the prompt when the sidecar reports a safe `compressed` result.

## Install / load

From this directory:

```sh
npm install
```

Then load as a local pi package/extension using your normal pi package settings or an explicit local path. The package manifest exposes:

```json
{ "pi": { "extensions": ["./src/index.ts"] } }
```

For quick local testing, pi also supports loading extension paths directly, for example:

```sh
pi -e /home/ayanp/pi-prompt-helper/src/index.ts
```

## Python setup

The sidecar uses only the Python standard library in conservative regex mode. For stronger POS/dependency safety gates, install spaCy and the small English model locally:

```sh
python3 -m pip install spacy
python3 -m spacy download en_core_web_sm
```

No remote LLM/API/MCP service is used. If spaCy or the model is unavailable, the sidecar falls back to a narrower regex/lexicon mode unless `--mode spacy` is explicitly requested.

## Behavior

- Skips extension-originated input.
- Skips slash commands by default.
- Skips prompts with attached images by default.
- Calls `python/squaizer_sidecar.py` locally and passes prompt text over stdin.
- Transforms only when JSON status is `compressed` and a different compressed text is returned.
- Fails open to the original prompt on errors, invalid JSON, unsafe validation, insufficient savings, or timeout.
- Compression is deletion-only: text is never reordered or paraphrased.
- Protected spans include fenced/inline code, URLs, emails, quoted strings, paths, numbers, code-like identifiers/options, negation, and constraint words.

Candidate deletions come from curated JSON lexicons in `python/resources/` for disfluencies, hedges, discourse markers, politeness/request framing, and intensifiers. These are candidates only; protected-span and spaCy/POS/dependency gates can still block deletion.

## Configuration

Environment variables:

- `PI_PROMPT_HELPER_ENABLED` — set `0`, `false`, `no`, `off`, or `disabled` to disable at startup.
- `PI_PROMPT_HELPER_PYTHON` or `PYTHON` — Python executable, default `python3`.
- `PI_PROMPT_HELPER_SIDECAR` — sidecar path override.
- `PI_PROMPT_HELPER_TIMEOUT_MS` — sidecar timeout, default `2000`.
- `PI_PROMPT_HELPER_MIN_SAVINGS` — minimum character savings ratio, default `0.03`.
- `PI_PROMPT_HELPER_MODE` — `auto`, `regex`, or `spacy`, default `auto`.
- `PI_PROMPT_HELPER_COMPRESS_SLASH` — set truthy to allow compression of slash-prefixed input.
- `PI_PROMPT_HELPER_COMPRESS_IMAGES` — set truthy to allow compression when images are attached.

Commands registered by the extension:

- `/exact <prompt>` — send the prompt exactly as written, bypassing compression.
- `/prompt-helper-toggle`
- `/prompt-helper-status`
- `/prompt-helper-stats`

## Development

```sh
npm run check
npm test
```

You can also call the sidecar directly:

```sh
echo 'Please, um, basically summarize this very small note.' | python3 python/squaizer_sidecar.py --mode regex --pretty
```

## Limitations

This is intentionally conservative. It targets small savings from obvious low-information prompt phrasing, not aggressive summarization. It may return the original prompt often, especially on code-heavy prompts or without spaCy. Politeness/tone words can be removed when enabled; disable the extension for prompts where tone is semantically important.
