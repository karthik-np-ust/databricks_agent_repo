"""Turns raw failure output into compact, high-signal context.

Real Databricks failures carry long JVM stack traces: hundreds of
`at com.databricks...` frames around one useful line. Sending the tail of that
to a model (or showing it to a human) buries the actual error. This keeps all
message lines, a few frames per stack block, drops duplicate lines, and when it
must truncate keeps the head (where the error is) plus a short tail.
"""

from __future__ import annotations

import re

_JVM_FRAME = re.compile(r"^\s*at [\w$.<>]+\(.*\)\s*$")
_MORE = re.compile(r"^\s*\.\.\. \d+ more\s*$")


def condense_logs(text: str, max_chars: int, frames_per_block: int = 5) -> str:
    out: list[str] = []
    seen: set[str] = set()
    frames = 0
    for line in (text or "").splitlines():
        if _JVM_FRAME.match(line) or _MORE.match(line):
            frames += 1
            if frames <= frames_per_block:
                out.append(line)
            elif frames == frames_per_block + 1:
                out.append("\t... (further stack frames omitted)")
            continue
        frames = 0
        key = line.strip()
        if key and key in seen:  # the same error message often repeats per task and trace
            continue
        seen.add(key)
        out.append(line)
    condensed = "\n".join(out)
    if len(condensed) <= max_chars:
        return condensed
    head = int(max_chars * 0.75)
    tail = max_chars - head - 40
    return condensed[:head] + "\n... [truncated] ...\n" + condensed[-tail:]
