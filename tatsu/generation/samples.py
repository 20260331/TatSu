# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
"""Finite, bounded generation of inputs accepted by a TatSu PEG grammar.

The generator unfolds the grammar model into concrete strings:

* choices are enumerated one option at a time,
* optional parts and closures are bounded by a depth budget,
* lookaheads (``&``/``!``) consume no input and the constraint they
  impose is enforced by parsing every generated candidate,
* empty and nullable rules naturally yield the empty string,
* recursion (direct or mutual) is bounded both by a rule-call depth
  budget and by an active rule-name stack.

Nothing generated here is trusted: a string is returned only if the
grammar's own parser accepts it.  Verification always runs **without
user semantics**, so semantic actions are never executed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from itertools import islice, product
from typing import Any

from .. import peg
from ..peg import (
    EOF,
    EOL,
    NIL,
    Alert,
    BasedRule,
    BoolMeta,
    Call,
    Choice,
    Closure,
    Comment,
    Constant,
    Cut,
    Dot,
    EmptyClosure,
    EOLComment,
    Fail,
    FloatMeta,
    Gather,
    Grammar,
    Group,
    IntMeta,
    Join,
    Lookahead,
    Model,
    Named,
    NamedList,
    NameMeta,
    NegativeLookahead,
    Option,
    Optional,
    Override,
    OverrideList,
    Pattern,
    PositiveClosure,
    PositiveGather,
    PositiveJoin,
    Rule,
    RuleInclude,
    Sequence,
    SkipGroup,
    SkipTo,
    Synth,
    Token,
    UIntMeta,
    Void,
)
from ..util.undefined import Undefined, UndefinedType
from .regexgen import RegexGenError, regex_samples


class GenerationError(ValueError):
    """No finite legal inputs could be produced from the grammar."""


# maximum number of candidate fragments kept per sequence element
_POOL_LIMIT = 12
# extra repetitions generated for closures within a single unfolding
_REPEAT_LIMIT = 3
# names tried when an @name token is needed
_NAME_CANDIDATES = ('x', 'y', 'z', 'name', 'item')
# filler generated before the target of a SkipTo (``->``) expression
_SKIP_FILLERS = ('', 'z', 'z z')
_SKIP_FILLERS_NOWS = ('', 'zz')

_WS_CANDIDATES = (' ', '\t', '\n')

# leaves that match the empty string
_EMPTY_LEAVES = frozenset(
    {
        Constant,
        Alert,
        Cut,
        EOF,
        Void,
        Comment,
        EOLComment,
        EmptyClosure,
    },
)
# leaves that can never lead to an accepted input
_DEAD_LEAVES = frozenset({NIL, Fail, Synth})
# nodes whose single child is generated unchanged
_WRAPPER_LEAVES = frozenset(
    {
        Group,
        SkipGroup,
        Named,
        NamedList,
        Override,
        OverrideList,
        Option,
    },
)


class SampleGenerator:
    """Generate finite inputs accepted by a compiled :class:`Grammar`.

    :param grammar: a compiled TatSu grammar.
    :param start: name of the start rule (defaults to the grammar's).
    """

    def __init__(self, grammar: Grammar, *, start: str | None = None):
        if not isinstance(grammar, Grammar):
            raise TypeError(
                f'expected a Grammar, got {type(grammar).__name__}',
            )
        self.grammar = grammar
        name = (
            start or grammar.config.effective_start_rule_name() or grammar.rules[0].name
        )
        if name not in grammar.rulemap:
            raise KeyError(f'unknown start rule {name!r}')
        self.start = name
        self.sep = self._choose_separator(grammar.config.whitespace)
        self._pattern_cache: dict[int, list[str]] = {}
        self._unsupported: list[str] = []
        self._ctx: Any = None
        self._config: Any = None
        self._handlers: dict[type, Any] = self._build_handlers()

    def _build_handlers(self) -> dict[type, Any]:
        return {
            Token: self._token,
            Dot: self._dot,
            Pattern: self._pattern,
            EOL: self._eol,
            NameMeta: self._name_meta,
            IntMeta: self._int_meta,
            UIntMeta: self._int_meta,
            FloatMeta: self._float_meta,
            BoolMeta: self._bool_meta,
            **dict.fromkeys(_WRAPPER_LEAVES, self._wrapper),
            Sequence: self._sequence,
            Choice: self._choice,
            Optional: self._optional,
            Closure: self._closure,
            PositiveClosure: self._positive_closure,
            Join: self._join_optional,
            Gather: self._join_optional,
            PositiveJoin: self._join_positive,
            PositiveGather: self._join_positive,
            Lookahead: self._lookahead,
            NegativeLookahead: self._lookahead,
            SkipTo: self._skip_to,
            Call: self._call,
            RuleInclude: self._rule_include,
            Rule: self._rule,
            BasedRule: self._based_rule,
        }

    @staticmethod
    def _choose_separator(whitespace: str | UndefinedType | None) -> str:
        """A literal the parser's whitespace expression accepts."""
        if whitespace is Undefined:
            return ' '
        if not isinstance(whitespace, str) or not whitespace:
            return ''
        try:
            matcher = re.compile(whitespace)
        except re.error:
            return ' '
        for c in _WS_CANDIDATES:
            if matcher.match(c):
                return c
        return ''

    # -- public API -----------------------------------------------------

    def generate(
        self,
        *,
        count: int = 5,
        max_depth: int = 4,
        attempts: int = 300,
    ) -> list[str]:
        """Return up to ``count`` distinct legal inputs.

        :param count: maximum number of samples wanted.
        :param max_depth: maximum recursive rule-call unfolding depth.
        :param attempts: maximum number of candidates verified.
        :raises GenerationError: if not a single accepted input is found.
        """
        if count < 1:
            raise ValueError('count must be a positive integer')
        if max_depth < 0:
            raise ValueError('max_depth must be >= 0')
        if attempts < 1:
            raise ValueError('attempts must be a positive integer')

        start_rule = self.grammar.rulemap[self.start]
        results: list[str] = []
        accepted: set[str] = set()
        # inputs differing only in horizontal whitespace count once
        accepted_keys: set[str] = set()
        tried: set[str] = set()
        used = 0

        for depth in range(max_depth + 1):
            if len(results) >= count or used >= attempts:
                break
            per_depth = attempts - used
            for raw in islice(
                self._gen(start_rule, depth, (self.start,)),
                per_depth,
            ):
                if used >= attempts or len(results) >= count:
                    break
                # try the most compact form first; the parser is the
                # final authority, and forms needing spaces fall back
                # to spaced/raw variants
                for text in self._variants(raw, tried, accepted):
                    tried.add(text)
                    used += 1
                    key = self._canonical(text)
                    if self._accepted(text) and key not in accepted_keys:
                        accepted.add(text)
                        accepted_keys.add(key)
                        results.append(text)
                        break
                if len(results) >= count:
                    break

        if not results:
            details = ''
            if self._unsupported:
                details = '; unsupported pattern constructs: ' + ', '.join(
                    sorted(set(self._unsupported))
                )
            raise GenerationError(
                f'could not generate any input accepted by {self.start!r}'
                f' within max_depth={max_depth}{details}',
            )

        results.sort(key=lambda s: (len(s), s))
        return results[:count]

    # -- verification ---------------------------------------------------

    def _accepted(self, text: str) -> bool:
        """Parse ``text`` with the grammar, never running user semantics."""
        optimized = self.grammar.optimized()
        if self._ctx is None:
            config = optimized.config.hard_override(
                semantics=None,
                start=self.start,
                parseinfo=False,
                trace=False,
            )
            self._config = config
            self._ctx = peg.ModelContext(optimized.rules, config=config)
        try:
            self._ctx.parse(text, config=self._config)
        except Exception:  # noqa: BLE001
            # only a clean parse counts; semantic code is never installed
            return False
        return True

    # -- generation -----------------------------------------------------

    def _gen(
        self,
        node: Model,
        depth: int,
        stack: tuple[str, ...],
    ) -> Iterator[str]:
        t = type(node)
        handler = self._handlers.get(t)
        if handler is not None:
            yield from handler(node, depth, stack)
        elif t in _EMPTY_LEAVES:
            yield ''
        elif t in _DEAD_LEAVES:
            return
        else:
            raise GenerationError(
                f'unsupported grammar construct: {type(node).__name__}',
            )

    # The parser only skips whitespace before tokens, rule calls and
    # meta matchers; patterns and '.' are matched at the cursor.  So
    # boundary fragments carry their own leading separator, while
    # sequences just concatenate.

    def _token(self, node: Token, depth: int, stack) -> Iterator[str]:
        _ = depth, stack
        yield self.sep + node.token

    def _dot(self, node: Dot, depth: int, stack) -> Iterator[str]:
        _ = node, depth, stack
        yield 'a'

    def _pattern(self, node: Pattern, depth: int, stack) -> Iterator[str]:
        _ = depth, stack
        yield from self._pattern_samples(node)

    def _eol(self, node: EOL, depth: int, stack) -> Iterator[str]:
        _ = node, depth, stack
        yield '\n'

    def _name_meta(self, node: NameMeta, depth: int, stack) -> Iterator[str]:
        _ = node, depth, stack
        for name in self._name_samples():
            yield self.sep + name

    def _int_meta(self, node, depth: int, stack) -> Iterator[str]:
        _ = node, depth, stack
        yield self.sep + '0'
        yield self.sep + '1'

    def _float_meta(self, node: FloatMeta, depth: int, stack) -> Iterator[str]:
        _ = node, depth, stack
        yield self.sep + '1.0'

    def _bool_meta(self, node: BoolMeta, depth: int, stack) -> Iterator[str]:
        _ = node, depth, stack
        yield self.sep + 'true'

    def _wrapper(self, node, depth: int, stack) -> Iterator[str]:
        yield from self._gen(node.exp, depth, stack)

    def _sequence(self, node: Sequence, depth: int, stack) -> Iterator[str]:
        pools: list[list[str]] = []
        for element in node.sequence:
            pool = list(islice(self._gen(element, depth, stack), _POOL_LIMIT))
            if not pool:
                return
            pools.append(pool)
        for combo in product(*pools):
            yield ''.join(combo)

    def _choice(self, node: Choice, depth: int, stack) -> Iterator[str]:
        for option in node.options:
            exp = option.exp if isinstance(option, Option) else option
            yield from self._gen(exp, depth, stack)

    def _optional(self, node: Optional, depth: int, stack) -> Iterator[str]:
        # shortest derivation first
        yield ''
        yield from self._gen(node.exp, depth, stack)

    def _closure(self, node: Closure, depth: int, stack) -> Iterator[str]:
        reps = range(0, min(depth, _REPEAT_LIMIT) + 1)
        yield from self._repeat(node.exp, reps, depth, stack)

    def _positive_closure(
        self,
        node: PositiveClosure,
        depth: int,
        stack,
    ) -> Iterator[str]:
        reps = range(1, max(min(depth, _REPEAT_LIMIT), 1) + 1)
        yield from self._repeat(node.exp, reps, depth, stack)

    def _join_optional(self, node: Join, depth: int, stack) -> Iterator[str]:
        yield from self._join(node, depth, stack, positive=False)

    def _join_positive(self, node: Join, depth: int, stack) -> Iterator[str]:
        yield from self._join(node, depth, stack, positive=True)

    def _lookahead(self, node, depth: int, stack) -> Iterator[str]:
        _ = node, depth, stack
        # consumes nothing; the asserted condition is checked by the
        # parse verification step
        yield ''

    def _skip_to(self, node: SkipTo, depth: int, stack) -> Iterator[str]:
        yield from self._skip_to_target(node, depth, stack)

    def _call(
        self,
        node: Call,
        depth: int,
        stack: tuple[str, ...],
    ) -> Iterator[str]:
        name = node.name
        rule = node._rule or self.grammar.rulemap.get(name)
        if rule is None or not isinstance(rule, Rule):
            return
        if name in stack:
            # recursive re-entry: the depth budget bounds unfolding,
            # and only the empty derivation keeps the branch finite
            if depth <= 0:
                if rule._nullable:
                    yield ''
                return
            depth -= 1
        # acyclic calls cost no depth: a finite grammar terminates anyway
        yield from self._gen(rule, depth, (*stack, name))

    def _rule_include(
        self,
        node: RuleInclude,
        depth: int,
        stack,
    ) -> Iterator[str]:
        if node._exp is not None:
            yield from self._gen(node._exp, depth, stack)

    def _rule(self, node: Rule, depth: int, stack) -> Iterator[str]:
        yield from self._gen(node.exp, depth, stack)

    def _based_rule(self, node: BasedRule, depth: int, stack) -> Iterator[str]:
        yield from self._gen(node.rhs, depth, stack)

    def _repeat(
        self,
        exp: Model,
        reps: range,
        depth: int,
        stack: tuple[str, ...],
    ) -> Iterator[str]:
        pool = list(islice(self._gen(exp, depth, stack), _POOL_LIMIT))
        if not pool:
            return
        for n in reps:
            for combo in product(pool, repeat=n):
                yield ''.join(combo)

    def _join(
        self,
        node: Join,
        depth: int,
        stack: tuple[str, ...],
        *,
        positive: bool,
    ) -> Iterator[str]:
        elements = list(islice(self._gen(node.exp, depth, stack), _POOL_LIMIT))
        separators = list(islice(self._gen(node.sep, depth, stack), _POOL_LIMIT))
        if not elements or not separators:
            return
        lo = 1 if positive else 0
        hi = max(min(depth, _REPEAT_LIMIT), lo)
        for n in range(lo, hi + 1):
            if n == 0:
                yield ''
                continue
            for elem_combo in product(elements, repeat=n):
                for sep in separators:
                    out = elem_combo[0]
                    for elem in elem_combo[1:]:
                        out += sep + elem
                    yield out

    def _skip_to_target(
        self,
        node: SkipTo,
        depth: int,
        stack: tuple[str, ...],
    ) -> Iterator[str]:
        fillers = _SKIP_FILLERS if self.sep else _SKIP_FILLERS_NOWS
        for target in islice(self._gen(node.exp, depth, stack), _POOL_LIMIT):
            for filler in fillers:
                yield filler + target

    def _pattern_samples(self, node: Pattern) -> Iterator[str]:
        key = id(node)
        if key not in self._pattern_cache:
            try:
                samples = regex_samples(node.pattern)
            except RegexGenError as e:
                self._unsupported.append(str(e))
                samples = []
            self._pattern_cache[key] = samples
        yield from self._pattern_cache[key]

    def _name_samples(self) -> Iterator[str]:
        keywords = self.grammar.keywords
        for name in _NAME_CANDIDATES:
            if name not in keywords:
                yield name
                return

    @staticmethod
    def _canonical(text: str) -> str:
        """Whitespace-insensitive identity of a sample (EOLs preserved)."""
        return re.sub(r'[ \t]+', '', text)

    @staticmethod
    def _variants(
        raw: str,
        tried: set[str],
        accepted: set[str],
    ) -> list[str]:
        """Candidate renderings of a raw sample, shortest first."""
        compact = re.sub(r'[ \t]+', '', raw)
        collapsed = re.sub(r'[ \t]+', lambda m: m.group(0)[0], raw).strip(' \t')
        variants: list[str] = []
        seen: set[str] = set()
        for text in (compact, collapsed, raw):
            if text not in seen and text not in tried and text not in accepted:
                seen.add(text)
                variants.append(text)
        return variants


def generate(
    grammar: Grammar,
    /,
    *,
    count: int = 5,
    max_depth: int = 4,
    attempts: int = 300,
    start: str | None = None,
) -> list[str]:
    """Generate up to ``count`` finite legal inputs for ``grammar``.

    Every returned string is accepted by the grammar's parser when
    parsed from ``start`` without executing user semantics.

    :param grammar: a compiled TatSu grammar.
    :param count: maximum number of distinct samples to return.
    :param max_depth: maximum recursive rule-call unfolding depth.
    :param attempts: maximum number of candidates verified.
    :param start: name of the start rule (defaults to the grammar's).
    """
    return SampleGenerator(grammar, start=start).generate(
        count=count,
        max_depth=max_depth,
        attempts=attempts,
    )
