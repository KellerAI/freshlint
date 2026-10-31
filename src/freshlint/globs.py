"""Gitignore-flavoured glob matching for project-relative POSIX paths.

``fnmatch`` treats ``*`` as matching ``/`` and has no ``**``, so freshlint uses its own
small translator:

* ``*`` matches any run of characters except ``/``
* ``?`` matches one character except ``/``
* ``[...]`` is a character class
* ``**/`` matches zero or more directories; a trailing ``/**`` matches everything below
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache


@lru_cache(maxsize=256)
def compile_glob(pattern: str) -> re.Pattern[str]:
    if pattern.startswith("./"):
        pattern = pattern[2:]
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**", i):
                after = i + 2
                if after < n and pattern[after] == "/":
                    out.append("(?:[^/]*/)*")
                    i = after + 1
                    continue
                out.append(".*")
                i = after
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = end
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


def matches(pattern: str, path: str) -> bool:
    return compile_glob(pattern).match(path) is not None


def match_any(patterns: Iterable[str], path: str) -> bool:
    return any(matches(p, path) for p in patterns)
