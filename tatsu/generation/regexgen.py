# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
"""Bounded generation of strings matching a Python regular expression.

PEG ``Pattern`` expressions (``/.../`` and ``?"..."``) contain Python
regular expressions.  TatSu grammars only use a small, well behaved
fraction of the regexp language, so instead of relying on the private
:mod:`sre_parse`/``re._parser`` modules (which change across Python
releases), this module implements a tiny parser for the common
constructs and emits a finite set of short matches under a depth budget.

Every emitted string is checked with :func:`re.fullmatch` before being
returned, so unsupported regexp subtleties can never produce invalid
matches.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import product

from ..util.regextools import cached_re_compile


class RegexGenError(ValueError):
    """The regular expression uses constructs the sample generator cannot expand."""


# a compact alphabet used to find concrete characters for classes and '.'
_ALNUM = '012abcAB_:'
_PUNCT = ' ,.-+*/()=<>!?:;"\'#@$%&[]{}|\\~\n\t'
_ALPHABET = _ALNUM + _PUNCT + ''.join(chr(c) for c in range(32, 127)) + 'éα中'

_SIMPLE_ESCAPES = {
    'n': '\n',
    'r': '\r',
    't': '\t',
    'f': '\f',
    'v': '\v',
    'a': '\a',
    '0': '\0',
    'b': '\b',
}

_CHAR_CLASSES = {
    'd': r'\d',
    'D': r'\D',
    'w': r'\w',
    'W': r'\W',
    's': r'\s',
    'S': r'\S',
}

_ANCHORS = {'^', '$', 'A', 'Z', 'z', 'b', 'B'}

_HEX_ESCAPES = {'x': 2, 'u': 4, 'U': 8}


@dataclass(slots=True)
class _Node:
    pass


@dataclass(slots=True)
class _Char(_Node):
    char: str


@dataclass(slots=True)
class _Class(_Node):
    matcher: re.Pattern[str]


@dataclass(slots=True)
class _Any(_Node):
    pass


@dataclass(slots=True)
class _Anchor(_Node):
    pass


@dataclass(slots=True)
class _Seq(_Node):
    items: list[_Node]


@dataclass(slots=True)
class _Alt(_Node):
    branches: list[_Node]


@dataclass(slots=True)
class _Repeat(_Node):
    node: _Node
    minimum: int
    maximum: int | None  # None means unbounded


class _RegexParser:
    def __init__(self, pattern: str):
        self.pattern = pattern
        self.pos = 0

    def parse(self) -> _Node:
        node = self._alternative()
        if self.pos != len(self.pattern):
            raise RegexGenError(
                f'unparsed input in pattern {self.pattern!r} at {self.pos}',
            )
        return node

    def _alternative(self) -> _Node:
        branches = [self._sequence()]
        while self._peek() == '|':
            self.pos += 1
            branches.append(self._sequence())
        if len(branches) == 1:
            return branches[0]
        return _Alt(branches)

    def _sequence(self) -> _Node:
        items: list[_Node] = []
        while self._peek() not in {None, '|', ')'}:
            atom = self._atom()
            items.extend(self._quantified(atom))
        if len(items) == 1:
            return items[0]
        return _Seq(items)

    def _quantified(self, atom: _Node) -> list[_Node]:
        c = self._peek()
        minimum: int = 1
        maximum: int | None = 1
        if c == '*':
            minimum, maximum = 0, None
        elif c == '+':
            minimum, maximum = 1, None
        elif c == '?':
            minimum, maximum = 0, 1
        elif c == '{':
            quantified = self._brace_repeat()
            if quantified is None:
                return [atom]
            minimum, maximum = quantified
        else:
            return [atom]

        self.pos += 1
        # consume the non-greedy/possessive marker, semantics are the same here
        if self._peek() == '?':
            self.pos += 1
        return [_Repeat(atom, minimum, maximum)]

    def _brace_repeat(self) -> tuple[int, int | None] | None:
        end = self.pattern.find('}', self.pos)
        if end < 0:
            return None
        body = self.pattern[self.pos + 1 : end]
        if not body or not re.fullmatch(r'\d*|\d+,|\d+,\d+', body):
            return None
        if ',' in body:
            lo, hi = body.split(',', 1)
            maximum = int(hi) if hi else None
            minimum = int(lo) if lo else 0
        else:
            minimum = maximum = int(body)
        self.pos = end
        return minimum, maximum

    def _atom(self) -> _Node:
        c = self._next()
        if c is None:
            raise RegexGenError('unexpected end of pattern')
        if c == '(':
            return self._group()
        if c == '[':
            return self._charclass()
        if c == '\\':
            return self._escape()
        simple = {
            '.': _Any(),
            '^': _Anchor(),
            '$': _Anchor(),
        }
        if c in simple:
            return simple[c]
        return _Char(c)

    def _group(self) -> _Node:
        if self._peek() == '?':
            self.pos += 1
            c = self._next()
            if c == '#':  # comment
                depth = 1
                while depth:
                    cc = self._next()
                    if cc is None:
                        raise RegexGenError('unterminated (?#...) comment')
                    if cc == '(':
                        depth += 1
                    elif cc == ')':
                        depth -= 1
                return _Seq([])
            if c in {'=', '!', '<'}:
                raise RegexGenError(
                    'lookbehind/lookahead assertions in patterns are not supported',
                )
            flags = ''
            while c != ':':
                if c is None:
                    raise RegexGenError('unterminated group flags')
                if c == ')':  # flag-only group, like (?m)
                    return _Seq([])
                flags += c
                c = self._next()
        node = self._alternative()
        if self._next() != ')':
            raise RegexGenError('unterminated group')
        return node

    def _charclass(self) -> _Node:
        start = self.pos - 1
        if self._peek() == '^':
            self.pos += 1
        # a ']' as the first character (after '^') is a literal
        if self._peek() == ']':
            self.pos += 1
        while True:
            c = self._next()
            if c is None:
                raise RegexGenError('unterminated character class')
            if c == '\\':
                if self._next() is None:
                    raise RegexGenError('dangling backslash in character class')
            elif c == ']':
                break
        raw = self.pattern[start : self.pos]
        try:
            matcher = cached_re_compile(raw)
        except re.error as e:
            raise RegexGenError(f'invalid character class {raw!r}: {e}') from e
        return _Class(matcher)

    def _escape(self) -> _Node:
        c = self._next()
        if c is None:
            raise RegexGenError('dangling backslash')
        if c in _SIMPLE_ESCAPES:
            return _Char(_SIMPLE_ESCAPES[c])
        if c in _CHAR_CLASSES:
            return _Class(cached_re_compile(_CHAR_CLASSES[c]))
        if c in _ANCHORS:
            return _Anchor()
        if c in _HEX_ESCAPES:
            return _Char(chr(self._hex(_HEX_ESCAPES[c])))
        if c.isdigit():
            digits = c
            while len(digits) < 3:
                nxt = self._peek()
                if nxt is None or not nxt.isdigit():
                    break
                consumed = self._next()
                assert consumed is not None
                digits += consumed
            return _Char(chr(int(digits, 8)))
        # escaped literal punctuation
        return _Char(c)

    def _hex(self, n: int) -> int:
        text = self.pattern[self.pos : self.pos + n]
        if len(text) != n or not re.fullmatch(r'[0-9a-fA-F]+', text):
            raise RegexGenError(f'expected {n} hex digits')
        self.pos += n
        return int(text, 16)

    def _peek(self) -> str | None:
        if self.pos >= len(self.pattern):
            return None
        return self.pattern[self.pos]

    def _next(self) -> str | None:
        if self.pos >= len(self.pattern):
            return None
        c = self.pattern[self.pos]
        self.pos += 1
        return c


def _class_candidates(matcher: re.Pattern[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for c in _ALPHABET:
        if matcher.fullmatch(c) and c not in seen:
            seen.add(c)
            result.append(c)
            if len(result) >= limit:
                break
    return result


def _emit(node: _Node, budget: int, fanout: int) -> Iterator[str]:
    if isinstance(node, _Char):
        yield node.char
    elif isinstance(node, _Anchor):
        yield ''
    elif isinstance(node, _Any):
        yield 'a'
    elif isinstance(node, _Class):
        yield from _class_candidates(node.matcher, fanout)
    elif isinstance(node, _Seq):
        pools = [list(_emit(i, budget, fanout)) for i in node.items]
        if not all(pools) and node.items:
            return
        for combo in product(*pools):
            yield ''.join(combo)
    elif isinstance(node, _Alt):
        for branch in node.branches:
            yield from _emit(branch, budget, fanout)
    elif isinstance(node, _Repeat):
        if node.maximum is None:
            upper = node.minimum + budget
        else:
            upper = min(node.maximum, node.minimum + budget)
        pool = list(_emit(node.node, budget, fanout))[:fanout]
        for n in range(node.minimum, upper + 1):
            for combo in product(pool, repeat=n):
                yield ''.join(combo)
    else:  # pragma: no cover
        raise RegexGenError(f'unknown regex AST node {type(node).__name__}')


def regex_samples(
    pattern: str,
    *,
    max_samples: int = 3,
    budget: int = 2,
    fanout: int = 3,
    max_attempts: int = 256,
) -> list[str]:
    """Return up to ``max_samples`` short strings matching ``pattern``.

    Only the regular expression subset used by typical PEG grammars is
    understood (literals, escapes, character classes, groups,
    alternation, anchors and the standard quantifiers).  Results are
    always verified with :func:`re.fullmatch`.

    :raises RegexGenError: if the pattern uses unsupported constructs.
    """
    try:
        compiled = cached_re_compile(pattern)
    except re.error as e:
        raise RegexGenError(f'invalid regular expression {pattern!r}: {e}') from e

    tree = _RegexParser(pattern).parse()

    result: list[str] = []
    seen: set[str] = set()
    attempts = 0
    for candidate in _emit(tree, budget=max(budget, 1), fanout=fanout):
        attempts += 1
        if attempts > max_attempts:
            break
        if candidate in seen:
            continue
        if compiled.fullmatch(candidate) is None:
            continue
        seen.add(candidate)
        result.append(candidate)
        if len(result) >= max_samples:
            break

    # shortest first, keeping the alphabet's preference order on ties
    return sorted(result, key=len)[:max_samples]
