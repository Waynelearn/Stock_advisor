"""Phase-3 grounding guardrail: *no unsourced number ships*.

The Phase-2 agent grounds its numbers by calling deterministic tools and gets a
**claims ledger** (`[{tool, args, result}]`) back. This module is the contract
check on that ledger: it scans the model's prose for numeric claims and flags any
that are **not backed by a value the tools actually returned** (or a number the
user themselves supplied in the question).

It is deliberately conservative to avoid false alarms:
  * Bare small integers (no `$`, no `%`, no decimal, magnitude < 50) are treated
    as trivia — days-to-expiry, counts, ordinals — and never flagged.
  * A claim is grounded if some ledger/question number matches within a relative
    tolerance (rounding in prose is fine), compared sign-insensitively (prose
    often says "loss of $1,700" while the tool returns -1700).

It also stamps a one-line **risk/disclaimer envelope** on answers that give
actionable advice (buy/sell/roll/close/…), so actionable output is never bare.

Pure-Python, no network — covered by ``smoke_test``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# A numeric token, optionally prefixed with $ / sign and suffixed with %.
# Captures the surrounding markers so we can tell a price/percent from a count.
_NUM_RE = re.compile(r"(?P<dollar>\$)?(?P<sign>[+-])?(?P<num>\d[\d,]*(?:\.\d+)?)(?P<pct>\s?%)?")

# Below this magnitude, a bare integer (no $, %, or decimal) is considered
# trivia (DTE, contract count, ordinal) and not subject to grounding.
_TRIVIAL_MAX = 50.0
# A claim matches a ledger number within this relative tolerance (prose rounds).
_REL_TOL = 0.015
# Percent claims also match within this absolute tolerance (points).
_PCT_ABS_TOL = 0.2

RISK_DISCLAIMER = (
    "⚠️ Not financial advice. Options can expire worthless; size to "
    "what you can lose."
)

# Words that make an answer "actionable" and thus warrant the risk envelope.
_ACTION_WORDS = re.compile(
    r"\b(buy|sell|short|long|roll|close|exit|add|trim|hedge|take profit|"
    r"stop[- ]?loss|recommend|should (?:buy|sell|close|roll|add|exit)|"
    r"i'd (?:buy|sell|close|roll|add|exit))\b",
    re.IGNORECASE,
)


@dataclass
class GuardrailReport:
    """Result of checking one answer against its ledger."""
    ok: bool = True
    checked: int = 0
    grounded: int = 0
    unsourced: list[dict] = field(default_factory=list)
    disclaimer_added: bool = False

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "grounded": self.grounded,
            "unsourced": self.unsourced,
            "disclaimer_added": self.disclaimer_added,
        }


def _walk_numbers(obj) -> list[float]:
    """Recursively collect every numeric leaf from a parsed JSON value."""
    out: list[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_walk_numbers(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_walk_numbers(v))
    elif isinstance(obj, str):
        # Tool results are JSON strings; numbers may also be embedded in text.
        for m in _NUM_RE.finditer(obj):
            try:
                out.append(float(m.group("num").replace(",", "")))
            except ValueError:
                continue
    return out


def collect_ledger_numbers(ledger: list[dict]) -> set[float]:
    """Every number the tools returned (and the args they were called with)."""
    nums: set[float] = set()
    for entry in ledger or []:
        result = entry.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (ValueError, TypeError):
                pass
        for n in _walk_numbers(result):
            nums.add(abs(round(n, 4)))
        for n in _walk_numbers(entry.get("args", {})):
            nums.add(abs(round(n, 4)))
    return nums


def extract_claim_numbers(text: str) -> list[tuple[float, str]]:
    """Numbers in prose that should be grounded, as (abs_value, raw_token).

    Trivia (bare small integers) is excluded so counts/ordinals don't trip it.
    """
    claims: list[tuple[float, str]] = []
    for m in _NUM_RE.finditer(text or ""):
        raw_num = m.group("num")
        try:
            value = float(raw_num.replace(",", ""))
        except ValueError:
            continue
        has_dollar = bool(m.group("dollar"))
        has_pct = bool(m.group("pct"))
        has_decimal = "." in raw_num
        trivial = (
            not has_dollar and not has_pct and not has_decimal
            and value < _TRIVIAL_MAX
        )
        if trivial:
            continue
        claims.append((value, m.group(0).strip()))
    return claims


def _is_grounded(value: float, allowed: set[float]) -> bool:
    target = abs(round(value, 4))
    if target in allowed:
        return True
    for g in allowed:
        denom = max(abs(g), 1.0)
        if abs(target - g) / denom <= _REL_TOL:
            return True
        if abs(target - g) <= _PCT_ABS_TOL:  # percents / small points
            return True
    return False


def needs_disclaimer(text: str) -> bool:
    return bool(_ACTION_WORDS.search(text or ""))


def check(text: str, ledger: list[dict], *, question: str = "") -> GuardrailReport:
    """Check ``text``'s numeric claims against the ledger (+ question numbers)."""
    allowed = collect_ledger_numbers(ledger)
    # Numbers the user supplied are fair game to echo back.
    for value, _ in extract_claim_numbers(question):
        allowed.add(abs(round(value, 4)))

    report = GuardrailReport()
    for value, raw in extract_claim_numbers(text):
        report.checked += 1
        if _is_grounded(value, allowed):
            report.grounded += 1
        else:
            report.unsourced.append({"value": value, "raw": raw})
    report.ok = not report.unsourced
    return report


def apply(
    text: str,
    ledger: list[dict],
    *,
    question: str = "",
    add_disclaimer: bool = True,
    annotate_unsourced: bool = True,
) -> tuple[str, GuardrailReport]:
    """Run the guardrail and return (possibly-annotated text, report).

    Non-blocking by design: unsourced numbers are surfaced with a warning footer
    rather than redacted, so the user still sees the answer but knows which
    figures aren't tool-backed.
    """
    report = check(text, ledger, question=question)
    out = text or ""

    if annotate_unsourced and report.unsourced:
        figs = ", ".join(u["raw"] for u in report.unsourced)
        out += (
            f"\n\n⚠️ Unverified figure(s) not backed by live data: {figs}. "
            "Treat with caution."
        )

    if add_disclaimer and needs_disclaimer(out) and RISK_DISCLAIMER not in out:
        out += f"\n\n{RISK_DISCLAIMER}"
        report.disclaimer_added = True

    return out, report
