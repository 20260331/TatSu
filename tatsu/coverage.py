# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
"""Final-derivation coverage recording and deterministic corpus reduction.

The standard parse trace reports every rule entry and every speculative
alternative, including the ones that fail and are later backtracked.  The
tools in this module instead record only the rules and choice alternatives
that the *final, successful* derivation of an input actually adopts.

Usage::

    import tatsu
    from tatsu.coverage import DerivationCoverage, reduce_corpus

    grammar = tatsu.compile(GRAMMAR)

    coverage = DerivationCoverage()
    grammar.parse(text, derivation_coverage=coverage)
    print(coverage.rules)        # {'start', ...}
    print(coverage.branches)     # {'word#0': {0, 1}, ...}

    # keep a smaller subset of the corpus covering the same rules/branches
    reduced = reduce_corpus(grammar, corpus)
    assert reduced.coverage.events == coverage_of_the_full_corpus
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from operator import itemgetter
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from .config import ParserConfig


# event kinds
RULE = 'rule'
BRANCH = 'branch'

# An event is one of:
#   (RULE, rule_name)
#   (BRANCH, choice_id, option_index)
Event = tuple[Any, ...]


class DerivationCoverage:
    """Records rules and alternatives adopted by successful parses.

    A recorder can be passed to ``parse`` through the
    ``derivation_coverage`` setting and reused across many inputs, in which
    case the recorded coverage is the union accumulated over all successful
    parses.  Failed parses never contribute any coverage.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Drop all accumulated coverage and parse-local state."""
        self._events: set[Event] = set()
        self.successful_parses: int = 0
        self.failed_parses: int = 0

        # parse-local state
        self._frames: list[set[Event]] = []
        self._memo: dict[Any, frozenset[Event]] = {}
        self._choice_owners: dict[int, dict[int, list[tuple[str, int]]]] = {}

    # ------------------------------------------------------------------
    # public view

    @property
    def rules(self) -> set[str]:
        """Names of the rules adopted by the successful derivations."""
        return {event[1] for event in self._events if event[0] == RULE}

    @property
    def branches(self) -> dict[str, set[int]]:
        """Choice identifiers mapped to the adopted option indexes."""
        result: dict[str, set[int]] = {}
        for event in self._events:
            if event[0] == BRANCH:
                _, choice_id, option_index = event
                result.setdefault(choice_id, set()).add(option_index)
        return result

    @property
    def events(self) -> frozenset[Event]:
        """The immutable set of covered rules and choice options."""
        return frozenset(self._events)

    def update(self, other: DerivationCoverage) -> None:
        """Accumulate the coverage recorded by another recorder."""
        self._events.update(other._events)
        self.successful_parses += other.successful_parses
        self.failed_parses += other.failed_parses

    def copy(self) -> DerivationCoverage:
        result = DerivationCoverage()
        result._events = set(self._events)
        result.successful_parses = self.successful_parses
        result.failed_parses = self.failed_parses
        return result

    def __or__(self, other: DerivationCoverage) -> DerivationCoverage:
        result = self.copy()
        result.update(other)
        return result

    def __ior__(self, other: DerivationCoverage) -> DerivationCoverage:
        self.update(other)
        return self

    def __len__(self) -> int:
        return len(self._events)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DerivationCoverage):
            return self._events == other._events
        if isinstance(other, frozenset):
            return self._events == set(other)
        return NotImplemented

    # mutable, hence unhashable, like sets and dictionaries
    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        rules = ', '.join(sorted(self.rules))
        branches = ', '.join(
            choice + ':{' + ','.join(map(str, sorted(options))) + '}'
            for choice, options in sorted(self.branches.items())
        )
        return f'{type(self).__name__}(rules={{{rules}}}, branches={{{branches}}})'

    # ------------------------------------------------------------------
    # parse lifecycle (invoked by the parser engine)

    def begin_parse(self) -> None:
        self._frames = [set()]
        self._memo = {}
        self._choice_owners = {}

    def discard_parse(self) -> None:
        if self._frames:
            self.failed_parses += 1
        self._frames = []
        self._memo = {}
        self._choice_owners = {}

    def commit_parse(self) -> None:
        if not self._frames:
            return
        # on a successful parse every nested frame has committed or
        # rolled back, so only the root frame survives
        self._events.update(self._frames[0])
        self.successful_parses += 1
        self._frames = []
        self._memo = {}
        self._choice_owners = {}

    # ------------------------------------------------------------------
    # speculation frames.
    #
    # Every rule call and every committed alternative opens a frame.
    # A frame that succeeds merges its events into the parent frame; a
    # frame that fails is rolled back and its events are discarded.  This
    # is what makes events recorded inside failed attempts disappear.

    def frame_open(self) -> None:
        if not self._frames:
            self._frames = [set()]
        self._frames.append(set())

    def frame_commit(self) -> None:
        events = self._frames.pop()
        self._frames[-1].update(events)

    def frame_rollback(self) -> None:
        self._frames.pop()

    def branch_taken(self, choice_id: str, option_index: int) -> None:
        self._frames[-1].add((BRANCH, choice_id, option_index))

    def memo_adopted(self, key: Any) -> None:
        """Re-attach the events stored with a memoized rule result."""
        stored = self._memo.get(key)
        if stored is not None:
            self._frames[-1].update(stored)

    def rule_succeeded(self, key: Any, name: str) -> None:
        """Adopt a successful rule call (fresh result or memoized one)."""
        top = self._frames[-1]
        stored = self._memo.get(key)
        if stored is None:
            # a freshly computed rule result keeps the events of the
            # derivation subtree adopted by its body
            stored = frozenset(top)
            self._memo[key] = stored
        else:
            # a memoized result: adopt the subtree events captured when
            # that result was first computed
            top.update(stored)
        top.add((RULE, name))

    # ------------------------------------------------------------------
    # choice identity for grammar-model execution

    def model_choice_id(self, choice: Any, ctx: Any) -> str:
        """Stable identifier for a choice in a grammar model.

        Choices are numbered in static pre-order within each rule, so the
        identifier is independent of parse order and input.
        """
        try:
            grammar = choice.grammar
        except RuntimeError:
            grammar = None

        grammar_id = id(grammar)
        if grammar_id not in self._choice_owners and grammar is not None:
            self._choice_owners[grammar_id] = self._build_choice_owners(grammar)
        owners = self._choice_owners.get(grammar_id) or {}

        candidates = owners.get(id(choice))
        if not candidates:
            return f'?#{id(choice)}'

        executing = ''
        callstack = getattr(ctx, 'callstack', None)
        if callstack:
            executing = callstack[-1].name
        for rule_name, ordinal in candidates:
            if rule_name == executing:
                return f'{rule_name}#{ordinal}'
        rule_name, ordinal = candidates[0]
        return f'{rule_name}#{ordinal}'

    @staticmethod
    def _build_choice_owners(grammar: Any) -> dict[int, list[tuple[str, int]]]:
        from .objectmodel import Node
        from .peg.choice import Choice

        seen: set[int] = set()

        def walk(node: Any) -> Iterable[Any]:
            if not isinstance(node, Node) or id(node) in seen:
                return
            seen.add(id(node))
            if isinstance(node, Choice):
                yield node
            try:
                fields_map = node.__pub__()
            except Exception:  # pragma: no cover - defensive
                return
            for value in fields_map.values():
                yield from descend(value)

        def descend(value: Any) -> Iterable[Any]:
            if isinstance(value, Node):
                yield from walk(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    yield from descend(item)
            elif isinstance(value, dict):
                for item in value.values():
                    yield from descend(item)

        owners: dict[int, list[tuple[str, int]]] = {}
        for rule in grammar.rules:
            seen.clear()
            for ordinal, choice in enumerate(walk(rule)):
                owners.setdefault(id(choice), []).append((rule.name, ordinal))
        return owners


@dataclass(frozen=True, eq=False)
class CorpusReduction:
    """Result of a deterministic corpus reduction.

    ``inputs`` holds the selected inputs in their original order;
    ``indices`` are their positions in the supplied corpus; ``coverage``
    is the union of their derivation coverage; ``failures`` lists the
    positions of inputs that failed to parse when failures were ignored.
    """

    inputs: tuple[Any, ...]
    indices: tuple[int, ...]
    coverage: DerivationCoverage
    failures: tuple[int, ...]

    def __iter__(self):
        return iter(self.inputs)

    def __len__(self) -> int:
        return len(self.inputs)

    def __getitem__(self, index: int) -> Any:
        return self.inputs[index]


def coverage_of(
    parser: Any,
    text: Any,
    /,
    *,
    start: str | None = None,
    config: ParserConfig | None = None,
    **settings: Any,
) -> DerivationCoverage:
    """Parse one input and return its final-derivation coverage."""
    coverage = DerivationCoverage()
    parser.parse(
        text,
        start=start,
        config=config,
        derivation_coverage=coverage,
        **settings,
    )
    return coverage


def reduce_corpus(
    parser: Any,
    inputs: Iterable[Any],
    /,
    *,
    start: str | None = None,
    config: ParserConfig | None = None,
    ignore_failures: bool = False,
    **settings: Any,
) -> CorpusReduction:
    """Deterministically select a small corpus with the same coverage.

    Each input is parsed with final-derivation recording enabled.  A
    deterministic greedy set cover then keeps the earliest inputs that
    together cover the same rules and choice options as the whole corpus.

    Parsing failures are re-raised by default, as they cannot be compared
    by coverage; pass ``ignore_failures=True`` to drop the failing inputs
    from the selection (their positions are reported in the result).
    """
    from .exceptions import FailedParse

    corpus: Sequence[Any] = tuple(inputs)

    parsed: list[tuple[int, Any, frozenset[Event]]] = []
    failures: list[int] = []
    for index, text in enumerate(corpus):
        coverage = DerivationCoverage()
        try:
            parser.parse(
                text,
                start=start,
                config=config,
                derivation_coverage=coverage,
                **settings,
            )
        except FailedParse:
            if not ignore_failures:
                raise
            failures.append(index)
            continue
        parsed.append((index, text, coverage.events))

    covered: set[Event] = set()
    chosen: list[tuple[int, Any, frozenset[Event]]] = []
    remaining = parsed
    while remaining:
        best_index = -1
        best_gain = 0
        best_position = 0
        best_events: frozenset[Event] = frozenset()
        for position, (index, _text, events) in enumerate(remaining):
            gain = len(events - covered)
            if gain > best_gain:
                best_gain = gain
                best_index = index
                best_position = position
                best_events = events
        if best_gain == 0:
            break
        chosen.append((best_index, remaining[best_position][1], best_events))
        covered.update(best_events)
        remaining = remaining[:best_position] + remaining[best_position + 1 :]

    chosen.sort(key=itemgetter(0))

    union = DerivationCoverage()
    for _index, _text, events in chosen:
        union._events.update(events)
    union.successful_parses = len(chosen)

    return CorpusReduction(
        inputs=tuple(text for _index, text, _events in chosen),
        indices=tuple(index for index, _text, _events in chosen),
        coverage=union,
        failures=tuple(failures),
    )
