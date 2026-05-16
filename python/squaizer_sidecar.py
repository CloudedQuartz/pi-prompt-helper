#!/usr/bin/env python3
"""Local deletion-only prompt compressor sidecar for pi-prompt-helper.

The compressor is deliberately conservative: it only deletes spans that match
curated resource lexicons, never rewrites or reorders text, and falls back to the
original prompt whenever validation cannot prove protected content was retained.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


RESOURCE_DIR = Path(__file__).resolve().parent / "resources"
DEFAULT_MIN_SAVINGS = 0.03


@dataclass(frozen=True)
class CandidateTerm:
    term: str
    category: str
    source_file: str
    fallback: bool = False
    sentence_initial: bool = False
    allow_spacy_critical: bool = False


@dataclass(frozen=True)
class DeleteSpan:
    start: int
    end: int
    term: str
    category: str


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    label: str


SPACY_NLP = None
SPACY_LOAD_ERROR = None


def load_spacy_model():
    global SPACY_NLP, SPACY_LOAD_ERROR
    if SPACY_NLP is not None or SPACY_LOAD_ERROR is not None:
        return SPACY_NLP
    try:
        import spacy  # type: ignore

        SPACY_NLP = spacy.load("en_core_web_sm")
    except Exception as exc:  # pragma: no cover - depends on local optional install
        SPACY_LOAD_ERROR = str(exc)
        SPACY_NLP = None
    return SPACY_NLP


def _iter_resource_files() -> Iterable[Path]:
    if not RESOURCE_DIR.exists():
        return []
    return sorted(RESOURCE_DIR.glob("*.json"))


def _term_from_item(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("term"), str):
        return item["term"]
    return None


def load_candidate_terms() -> list[CandidateTerm]:
    terms: list[CandidateTerm] = []
    for path in _iter_resource_files():
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("metadata", {}).get("kind") == "protected":
            continue
        categories = data.get("categories", {})
        if not isinstance(categories, dict):
            continue
        for category, items in categories.items():
            if not isinstance(items, list):
                continue
            for item in items:
                term = _term_from_item(item)
                if not term:
                    continue
                flags = item if isinstance(item, dict) else {}
                terms.append(
                    CandidateTerm(
                        term=term.lower(),
                        category=str(category),
                        source_file=path.name,
                        fallback=bool(flags.get("fallback", False)),
                        sentence_initial=bool(flags.get("sentence_initial", False)),
                        allow_spacy_critical=bool(flags.get("allow_spacy_critical", False)),
                    )
                )
    # Prefer longer phrases so "kind of" wins before "kind" if both ever exist.
    return sorted(terms, key=lambda item: (-len(item.term.split()), -len(item.term), item.term))


def load_protected_terms() -> set[str]:
    protected: set[str] = set()
    for path in _iter_resource_files():
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("metadata", {}).get("kind") != "protected":
            continue
        categories = data.get("categories", {})
        if not isinstance(categories, dict):
            continue
        for items in categories.values():
            if not isinstance(items, list):
                continue
            for item in items:
                term = _term_from_item(item)
                if term:
                    protected.add(term.lower())
    return protected


def add_regex_spans(text: str, spans: list[Span], pattern: str, label: str, flags: int = 0) -> None:
    for match in re.finditer(pattern, text, flags):
        if match.start() < match.end():
            spans.append(Span(match.start(), match.end(), label))


def protected_spans(text: str, protected_terms: set[str]) -> list[Span]:
    spans: list[Span] = []
    add_regex_spans(text, spans, r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)", "code_fence")
    add_regex_spans(text, spans, r"`[^`\n]+`", "inline_code")
    add_regex_spans(text, spans, r"\b(?:https?://|www\.)[^\s<>'\")]+", "url", re.IGNORECASE)
    add_regex_spans(text, spans, r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "email", re.IGNORECASE)
    add_regex_spans(text, spans, r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", "quoted_string")
    add_regex_spans(
        text,
        spans,
        r"(?<!\w)(?:~|\.{1,2})?/[^\s`'\"]+|(?<!\w)\.{1,2}/[^\s`'\"]+|\b[A-Za-z]:\\[^\n`'\"]+|\b[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+",
        "path",
    )
    add_regex_spans(text, spans, r"(?<![\w-])\d+(?:[.,:/-]\d+)*(?:%|[A-Za-z]+)?(?![\w-])", "number")
    add_regex_spans(text, spans, r"(?<!\w)--?[A-Za-z0-9][\w-]*", "option")
    add_regex_spans(text, spans, r"\b[A-Za-z_$][\w$]*\s*\(", "function_call")
    add_regex_spans(text, spans, r"\b[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+\b", "identifier")
    add_regex_spans(text, spans, r"\b[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+\b", "identifier")
    add_regex_spans(text, spans, r"\b[a-z]+[A-Z][A-Za-z0-9]*\b", "identifier")

    for term in sorted(protected_terms, key=len, reverse=True):
        escaped = re.escape(term).replace(r"\ ", r"\s+")
        add_regex_spans(text, spans, rf"(?<![A-Za-z0-9_-]){escaped}(?![A-Za-z0-9_-])", "protected_term", re.IGNORECASE)

    return merge_spans(spans)


def merge_spans(spans: list[Span]) -> list[Span]:
    if not spans:
        return []
    ordered = sorted(spans, key=lambda span: (span.start, span.end))
    merged: list[Span] = [ordered[0]]
    for span in ordered[1:]:
        last = merged[-1]
        if span.start <= last.end:
            merged[-1] = Span(last.start, max(last.end, span.end), f"{last.label}+{span.label}")
        else:
            merged.append(span)
    return merged


def mask_from_spans(length: int, spans: Iterable[Span]) -> list[bool]:
    mask = [False] * length
    for span in spans:
        for i in range(max(0, span.start), min(length, span.end)):
            mask[i] = True
    return mask


def has_masked(mask: list[bool], start: int, end: int) -> bool:
    return any(mask[max(0, start) : min(len(mask), end)])


def spacy_critical_mask(text: str, protected_terms: set[str], mode: str) -> tuple[list[bool], str]:
    if mode == "regex":
        return [False] * len(text), "regex"
    nlp = load_spacy_model()
    if nlp is None:
        return [False] * len(text), "regex"

    critical = [False] * len(text)
    doc = nlp(text)
    critical_pos = {"NOUN", "PROPN", "VERB", "AUX", "NUM", "SYM", "X"}
    critical_dep = {
        "ROOT",
        "nsubj",
        "nsubjpass",
        "dobj",
        "obj",
        "pobj",
        "iobj",
        "attr",
        "neg",
        "aux",
        "auxpass",
        "prep",
        "agent",
        "ccomp",
        "xcomp",
    }
    for token in doc:
        lower = token.text.lower()
        is_critical = (
            token.ent_type_
            or token.pos_ in critical_pos
            or token.dep_ in critical_dep
            or token.like_url
            or token.like_email
            or token.like_num
            or lower in protected_terms
        )
        if is_critical:
            for i in range(token.idx, token.idx + len(token.text)):
                if 0 <= i < len(critical):
                    critical[i] = True
    return critical, "spacy"


def phrase_pattern(term: str) -> re.Pattern[str]:
    pieces = [re.escape(piece) for piece in term.split()]
    body = r"\s+".join(pieces)
    return re.compile(rf"(?<![A-Za-z0-9_-]){body}(?![A-Za-z0-9_-])", re.IGNORECASE)


def sentence_initial_context(text: str, start: int) -> bool:
    prefix = text[:start].rstrip()
    if not prefix:
        return True
    return prefix[-1] in ".!?\n"


def extend_delete_span(text: str, start: int, end: int) -> tuple[int, int]:
    new_start = start
    new_end = end

    if new_end < len(text) and text[new_end] in ",;:":
        new_end += 1
        while new_end < len(text) and text[new_end].isspace():
            new_end += 1
    elif new_end < len(text) and text[new_end].isspace():
        # Removing the following separator keeps "very important" -> "important"
        # and "can you please help" -> "can you help" without rewriting text.
        new_end += 1

    before = text[:new_start]
    trimmed_before = before.rstrip()
    if trimmed_before.endswith((",", ";", ":")):
        comma_index = len(trimmed_before) - 1
        if all(ch.isspace() for ch in text[comma_index + 1 : new_start]):
            new_start = comma_index
    elif new_start > 0 and text[new_start - 1].isspace() and (new_end >= len(text) or text[new_end - 1].isspace()):
        # For trailing terms, remove one preceding space rather than leaving a gap.
        if new_end >= len(text):
            new_start -= 1

    return new_start, new_end


def find_delete_spans(text: str, terms: list[CandidateTerm], protected_mask: list[bool], critical_mask: list[bool], mode_used: str) -> list[DeleteSpan]:
    spans: list[DeleteSpan] = []
    occupied_terms = [False] * len(text)
    for term in terms:
        if mode_used == "regex" and not term.fallback:
            continue
        pattern = phrase_pattern(term.term)
        for match in pattern.finditer(text):
            start, end = match.span()
            if term.sentence_initial and not sentence_initial_context(text, start):
                continue
            adj_start, adj_end = extend_delete_span(text, start, end)
            if has_masked(protected_mask, adj_start, adj_end):
                continue
            if not term.allow_spacy_critical and has_masked(critical_mask, start, end):
                continue
            # Only the matched term text is mutually exclusive. Adjusted spans
            # may legitimately share commas/spaces with neighboring candidates.
            if has_masked(occupied_terms, start, end):
                continue
            spans.append(DeleteSpan(adj_start, adj_end, match.group(0), term.category))
            for i in range(start, end):
                if 0 <= i < len(occupied_terms):
                    occupied_terms[i] = True
    return sorted(spans, key=lambda span: (span.start, span.end))


def reconstruct(text: str, delete_spans: Iterable[DeleteSpan]) -> str:
    mask = [False] * len(text)
    for span in delete_spans:
        for i in range(max(0, span.start), min(len(text), span.end)):
            mask[i] = True
    return "".join(ch for i, ch in enumerate(text) if not mask[i])


def is_subsequence(original: str, candidate: str) -> bool:
    pos = 0
    for char in candidate:
        pos = original.find(char, pos)
        if pos < 0:
            return False
        pos += 1
    return True


def protected_substrings_preserved(original: str, compressed: str, spans: Iterable[Span]) -> bool:
    cursor = 0
    for span in spans:
        piece = original[span.start : span.end]
        if not piece:
            continue
        found = compressed.find(piece, cursor)
        if found < 0:
            return False
        cursor = found + len(piece)
    return True


def compress(text: str, min_savings: float = DEFAULT_MIN_SAVINGS, mode: str = "auto") -> dict[str, Any]:
    if not text:
        return result("original", text, text, "empty")
    if mode not in {"auto", "spacy", "regex"}:
        return result("original", text, text, f"invalid mode {mode!r}")

    try:
        terms = load_candidate_terms()
        protected_terms = load_protected_terms()
    except Exception as exc:
        return result("original", text, text, f"resource_load_failed: {exc}")

    protected = protected_spans(text, protected_terms)
    protected_mask = mask_from_spans(len(text), protected)
    critical_mask, mode_used = spacy_critical_mask(text, protected_terms, mode)
    if mode == "spacy" and mode_used != "spacy":
        return result("original", text, text, "spacy_unavailable")

    delete_spans = find_delete_spans(text, terms, protected_mask, critical_mask, mode_used)
    if not delete_spans:
        return result("original", text, text, "no_safe_candidates", mode_used)

    compressed = reconstruct(text, delete_spans)
    if compressed == text or not compressed.strip():
        return result("original", text, text, "no_change", mode_used)
    if not validate(text, compressed, protected):
        return result("original", text, text, "validation_failed", mode_used)

    savings = (len(text) - len(compressed)) / max(1, len(text))
    if savings < min_savings:
        return result("original", text, text, "insufficient_savings", mode_used)

    payload = result("compressed", text, compressed, "ok", mode_used)
    payload["deleted"] = [
        {"text": text[span.start : span.end], "term": span.term, "category": span.category, "start": span.start, "end": span.end}
        for span in delete_spans
    ]
    return payload


def validate(original: str, compressed: str, protected: list[Span]) -> bool:
    if len(compressed) >= len(original):
        return False
    if not is_subsequence(original, compressed):
        return False
    if not protected_substrings_preserved(original, compressed, protected):
        return False
    if original.count("```") != compressed.count("```"):
        return False
    if original.count("~~~") != compressed.count("~~~"):
        return False
    return True


def result(status: str, original: str, text: str, reason: str, mode: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "text": text,
        "original_chars": len(original),
        "compressed_chars": len(text),
        "savings_ratio": (len(original) - len(text)) / max(1, len(original)),
        "reason": reason,
        "mode": mode or "unknown",
    }


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_mode(name: str, default: str) -> str:
    raw = os.environ.get(name, default)
    return raw if raw in {"auto", "spacy", "regex"} else default


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local deletion-only prompt compressor")
    parser.add_argument("prompt", nargs="?", help="Prompt text. If omitted, stdin is read.")
    parser.add_argument("--text", dest="text", help="Prompt text. Overrides positional prompt and stdin.")
    parser.add_argument("--min-savings", type=float, default=env_float("PI_PROMPT_HELPER_MIN_SAVINGS", DEFAULT_MIN_SAVINGS))
    parser.add_argument("--mode", choices=["auto", "spacy", "regex"], default=env_mode("PI_PROMPT_HELPER_MODE", "auto"))
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.text is not None:
        text = args.text
    elif args.prompt is not None:
        text = args.prompt
    else:
        text = sys.stdin.read()

    try:
        payload = compress(text, min_savings=args.min_savings, mode=args.mode)
    except Exception as exc:  # fail open for callers that parse JSON
        payload = result("original", text, text, f"error: {exc}")
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
