#!/usr/bin/env python3
"""Small local deletion-only prompt cleaner.

It removes a narrow set of low-information prompt phrases when protected text is
not touched. It never paraphrases, reorders, or sends text anywhere.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Iterable

DEFAULT_MIN_SAVINGS = 0.03


@dataclass(frozen=True)
class Candidate:
    phrase: str
    reason: str
    sentence_initial: bool = False


@dataclass(frozen=True)
class Span:
    start: int
    end: int


@dataclass(frozen=True)
class Deletion:
    start: int
    end: int
    text: str
    reason: str


CANDIDATES = [
    Candidate("could you please", "request framing", True),
    Candidate("can you please", "request framing", True),
    Candidate("would you please", "request framing", True),
    Candidate("could you", "request framing", True),
    Candidate("can you", "request framing", True),
    Candidate("would you", "request framing", True),
    Candidate("i think", "speaker hedge", True),
    Candidate("i guess", "speaker hedge", True),
    Candidate("i suppose", "speaker hedge", True),
    Candidate("please", "politeness"),
    Candidate("thank you", "politeness"),
    Candidate("thanks", "politeness"),
    Candidate("um", "disfluency"),
    Candidate("uh", "disfluency"),
    Candidate("basically", "low information adverb"),
    Candidate("actually", "low information adverb"),
    Candidate("literally", "low information adverb"),
    Candidate("very", "intensifier"),
    Candidate("really", "intensifier"),
    Candidate("quite", "intensifier"),
    Candidate("sort of", "hedge"),
    Candidate("kind of", "hedge"),
]

PROTECTED_TERMS = {
    "not",
    "no",
    "never",
    "without",
    "unless",
    "don't",
    "dont",
    "do not",
    "can't",
    "cant",
    "cannot",
    "won't",
    "wont",
    "avoid",
    "must",
    "only",
    "just",
    "always",
    "before",
    "after",
    "except",
    "required",
    "preserve",
    "keep",
    "delete",
    "remove",
}


def add(spans: list[Span], text: str, pattern: str, flags: int = 0) -> None:
    for match in re.finditer(pattern, text, flags):
        if match.start() < match.end():
            spans.append(Span(match.start(), match.end()))


def phrase_pattern(phrase: str) -> str:
    return rf"(?<![A-Za-z0-9_-]){re.escape(phrase).replace(r'\ ', r'\s+')}(?![A-Za-z0-9_-])"


def protected_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    add(spans, text, r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)")
    add(spans, text, r"`[^`\n]+`")
    add(spans, text, r"\b(?:https?://|www\.)[^\s<>'\")]+", re.IGNORECASE)
    add(spans, text, r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
    add(spans, text, r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")
    add(spans, text, r"(?<!\w)(?:~|\.{1,2})?/[^\s`'\"]+")
    add(spans, text, r"\b[A-Za-z]:\\[^\n`'\"]+")
    add(spans, text, r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+")
    add(spans, text, r"(?<![\w-])\d+(?:[.,:/-]\d+)*(?:%|[A-Za-z]+)?(?![\w-])")
    add(spans, text, r"(?<!\w)--?[A-Za-z0-9][\w-]*")
    add(spans, text, r"\b[A-Za-z_$][\w$]*\s*\(")
    add(spans, text, r"\b[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+\b")
    add(spans, text, r"\b[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+\b")
    add(spans, text, r"\b[a-z]+[A-Z][A-Za-z0-9]*\b")
    for term in sorted(PROTECTED_TERMS, key=len, reverse=True):
        add(spans, text, phrase_pattern(term), re.IGNORECASE)
    return merge(spans)


def merge(spans: Iterable[Span]) -> list[Span]:
    ordered = sorted(spans, key=lambda span: (span.start, span.end))
    merged: list[Span] = []
    for span in ordered:
        if not merged or span.start > merged[-1].end:
            merged.append(span)
        else:
            merged[-1] = Span(merged[-1].start, max(merged[-1].end, span.end))
    return merged


def mask_for(text: str, spans: Iterable[Span]) -> list[bool]:
    mask = [False] * len(text)
    for span in spans:
        for index in range(max(0, span.start), min(len(text), span.end)):
            mask[index] = True
    return mask


def touches(mask: list[bool], start: int, end: int) -> bool:
    return any(mask[max(0, start) : min(len(mask), end)])


def is_sentence_initial(text: str, start: int) -> bool:
    prefix = text[:start].rstrip()
    return not prefix or prefix[-1] in ".!?\n"


def extend_deletion(text: str, start: int, end: int) -> tuple[int, int]:
    new_start, new_end = start, end
    while new_end < len(text) and text[new_end] in " \t":
        new_end += 1
    if new_end < len(text) and text[new_end] in ",;":
        new_end += 1
        while new_end < len(text) and text[new_end] in " \t":
            new_end += 1
    elif new_start > 0 and text[new_start - 1] == " " and (new_end == len(text) or text[new_end] in "\n.,;:!?"):
        new_start -= 1
    return new_start, new_end


def find_deletions(text: str, protected: list[bool]) -> list[Deletion]:
    deletions: list[Deletion] = []
    occupied = [False] * len(text)
    for candidate in sorted(CANDIDATES, key=lambda item: (-len(item.phrase), item.phrase)):
        for match in re.finditer(phrase_pattern(candidate.phrase), text, re.IGNORECASE):
            start, end = match.span()
            if candidate.sentence_initial and not is_sentence_initial(text, start):
                continue
            if touches(protected, start, end) or touches(occupied, start, end):
                continue
            delete_start, delete_end = extend_deletion(text, start, end)
            if touches(protected, delete_start, delete_end):
                continue
            for index in range(delete_start, delete_end):
                if 0 <= index < len(occupied):
                    occupied[index] = True
            deletions.append(Deletion(delete_start, delete_end, match.group(0), candidate.reason))
    return sorted(deletions, key=lambda item: (item.start, item.end))


def reconstruct(text: str, deletions: Iterable[Deletion]) -> str:
    deleted = [False] * len(text)
    for deletion in deletions:
        for index in range(max(0, deletion.start), min(len(text), deletion.end)):
            deleted[index] = True
    return "".join(char for index, char in enumerate(text) if not deleted[index])


def payload(status: str, text: str, cleaned: str, reason: str, deletions: Iterable[Deletion] = ()) -> dict[str, object]:
    deleted = list(deletions)
    return {
        "status": status,
        "text": cleaned,
        "reason": reason,
        "original_chars": len(text),
        "cleaned_chars": len(cleaned),
        "savings_ratio": 0 if not text else (len(text) - len(cleaned)) / len(text),
        "mode": "regex",
        "deleted": [{"text": item.text, "reason": item.reason} for item in deleted],
    }


def clean(text: str, min_savings: float = DEFAULT_MIN_SAVINGS) -> dict[str, object]:
    if not text.strip():
        return payload("original", text, text, "blank")
    spans = protected_spans(text)
    protected = mask_for(text, spans)
    deletions = find_deletions(text, protected)
    if not deletions:
        return payload("original", text, text, "no_safe_deletions")
    cleaned = reconstruct(text, deletions)
    for span in spans:
        fragment = text[span.start : span.end]
        if fragment and fragment not in cleaned:
            return payload("original", text, text, "protected_text_changed")
    if cleaned == text:
        return payload("original", text, text, "unchanged")
    savings = (len(text) - len(cleaned)) / len(text)
    if savings < min_savings:
        return payload("original", text, text, "below_min_savings", deletions)
    return payload("cleaned", text, cleaned, "ok", deletions)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean low-information prompt text locally.")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--min-savings", type=float, default=DEFAULT_MIN_SAVINGS)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    if args.health:
        print(json.dumps({"ok": True, "mode": "regex"}, indent=2 if args.pretty else None))
        return 0

    result = clean(sys.stdin.read(), min_savings=args.min_savings)
    print(json.dumps(result, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
