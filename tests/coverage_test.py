# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
from __future__ import annotations

import types

import pytest

import tatsu
from tatsu.coverage import (
    CorpusReduction,
    DerivationCoverage,
    coverage_of,
    reduce_corpus,
)
from tatsu.exceptions import FailedParse


GRAMMAR = """
    start = word $ ;
    word = 'a' | 'b' ;
"""


def compile_model(grammar: str = GRAMMAR):
    return tatsu.compile(grammar)


def parse(grammar_model, text, **kwargs):
    coverage = DerivationCoverage()
    grammar_model.parse(text, derivation_coverage=coverage, **kwargs)
    return coverage


def generate_and_load_parser(grammar: str):
    code = tatsu.to_python_sourcecode(grammar, name='CoverageTest')
    module = types.ModuleType('coverage_test_generated_parser')
    module.__file__ = '<coverage_test_generated>'
    exec(compile(code, module.__file__, 'exec'), module.__dict__)
    return module.CoverageTestParser()


# ---------------------------------------------------------------------------
# final-derivation coverage


def test_records_rules_and_branches_of_successful_parse():
    model = compile_model()

    coverage = parse(model, 'a')

    assert coverage.rules == {'start', 'word'}
    assert coverage.branches == {'word#0': {0}}
    assert coverage.successful_parses == 1
    assert coverage.failed_parses == 0


def test_accumulates_across_successful_parses():
    model = compile_model()
    coverage = DerivationCoverage()

    model.parse('a', derivation_coverage=coverage)
    model.parse('b', derivation_coverage=coverage)

    assert coverage.branches == {'word#0': {0, 1}}
    assert coverage.successful_parses == 2


def test_failed_parse_contributes_nothing():
    model = compile_model()
    coverage = DerivationCoverage()

    with pytest.raises(FailedParse):
        model.parse('c', derivation_coverage=coverage)

    assert coverage.events == frozenset()
    assert coverage.rules == set()
    assert coverage.branches == {}
    assert coverage.failed_parses == 1

    # a later successful parse still records on the same recorder
    model.parse('a', derivation_coverage=coverage)
    assert coverage.rules == {'start', 'word'}
    assert coverage.successful_parses == 1


def test_failed_alternatives_are_not_recorded():
    model = compile_model(
        """
        start = x $ ;
        x = 'ab' | 'ac' ;
        """,
    )

    coverage = parse(model, 'ac')

    assert coverage.branches == {'x#0': {1}}


def test_rules_computed_in_backtracked_alternatives_are_adopted_via_memo():
    # ``x`` is computed successfully while trying the first alternative,
    # which then fails on the following token.  The winning alternative
    # reuses the memoized result, so ``x`` must still be covered.
    model = compile_model(
        """
        start = alt $ ;
        alt = x '!' | x '?' ;
        x = 'z' ;
        """,
    )

    coverage = parse(model, 'z ?')

    assert coverage.rules == {'start', 'alt', 'x'}
    assert coverage.branches == {'alt#0': {1}}


def test_rules_used_only_in_lookahead_are_not_recorded():
    model = compile_model(
        """
        start = &guard 'a' $ ;
        guard = 'a' ;
        """,
    )

    coverage = parse(model, 'a')

    assert 'guard' not in coverage.rules
    assert coverage.rules == {'start'}


def test_rules_in_closures_are_recorded():
    model = compile_model(
        r"""
        start = digits $ ;
        digits = digit+ ;
        digit = /\d/ ;
        """,
    )

    coverage = parse(model, '123')

    assert coverage.rules == {'start', 'digits', 'digit'}


def test_branches_taken_in_closures_accumulate():
    model = compile_model(
        """
        start = items $ ;
        items = item+ ;
        item = 'a' | 'b' ;
        """,
    )

    coverage = parse(model, 'a b a')

    assert coverage.branches == {'item#0': {0, 1}}


def test_left_recursion_records_the_final_derivation():
    model = compile_model(
        r"""
        start = e $ ;
        e = e '+' n | n ;
        n = /\d/ ;
        """,
    )

    base = parse(model, '1')
    assert base.rules == {'start', 'e', 'n'}
    assert base.branches == {'e#0': {1}}

    recursive = parse(model, '1 + 2')
    assert recursive.rules == {'start', 'e', 'n'}
    assert recursive.branches == {'e#0': {0, 1}}


def test_semantics_failures_contribute_nothing():
    class FailingSemantics:
        def word(self, _node):
            raise ValueError('boom')

    model = compile_model()
    coverage = DerivationCoverage()

    with pytest.raises(Exception):  # noqa: PT011, B017
        model.parse('a', semantics=FailingSemantics(), derivation_coverage=coverage)

    assert coverage.events == frozenset()


def test_coverage_is_disabled_by_default():
    model = compile_model()

    model.parse('a')

    assert model.newctx().coverage is None


def test_generated_parser_records_coverage():
    parser = generate_and_load_parser(GRAMMAR)
    coverage = DerivationCoverage()

    parser.parse('b', derivation_coverage=coverage)

    assert coverage.rules == {'start', 'word'}
    option_sets = list(coverage.branches.values())
    assert option_sets == [{1}]
    choice_id = next(iter(coverage.branches))
    assert choice_id.startswith('word:')


def test_parser_local_recorder_with_true():
    parser = generate_and_load_parser(GRAMMAR)

    parser.parse('a', derivation_coverage=True)
    assert isinstance(parser.coverage, DerivationCoverage)
    assert parser.coverage.rules == {'start', 'word'}

    # parsing without recording detaches the recorder
    parser.parse('a')
    assert parser.coverage is None


def test_coverage_of_helper():
    model = compile_model()

    coverage = coverage_of(model, 'a')

    assert coverage.rules == {'start', 'word'}
    assert coverage.branches == {'word#0': {0}}


def test_coverage_copy_union_and_equality():
    model = compile_model()
    first = coverage_of(model, 'a')
    second = coverage_of(model, 'b')

    combined = first | second
    assert combined.events == (first.events | second.events)
    assert combined == second | first

    first |= second
    assert first == combined


# ---------------------------------------------------------------------------
# deterministic corpus reduction


def test_reduce_corpus_keeps_equivalent_smaller_set():
    model = compile_model()
    corpus = ['b', 'a', 'b', 'a', 'b']

    reduced = reduce_corpus(model, corpus)

    assert isinstance(reduced, CorpusReduction)
    assert reduced.inputs == ('b', 'a')
    assert reduced.indices == (0, 1)
    assert reduced.coverage.events == (
        coverage_of(model, 'a').events | coverage_of(model, 'b').events
    )


def test_reduce_corpus_is_deterministic_and_ordered():
    model = compile_model(
        """
        start = item $ ;
        item = 'a' | 'b' | 'c' ;
        """,
    )
    corpus = ['c', 'a', 'b', 'c', 'a', 'b']

    first = reduce_corpus(model, corpus)
    second = reduce_corpus(model, corpus)

    assert first.indices == second.indices
    # selected inputs keep their original corpus order
    assert first.inputs == ('c', 'a', 'b')
    assert first.indices == (0, 1, 2)
    assert set(first.inputs) == {'a', 'b', 'c'}


def test_reduce_corpus_with_uniform_inputs_keeps_one():
    model = compile_model()
    reduced = reduce_corpus(model, ['a', 'a', 'a'])

    assert reduced.inputs == ('a',)
    assert reduced.indices == (0,)


def test_reduce_corpus_raises_on_failure_by_default():
    model = compile_model()
    with pytest.raises(FailedParse):
        reduce_corpus(model, ['a', 'z', 'b'])


def test_reduce_corpus_can_ignore_failures():
    model = compile_model()
    reduced = reduce_corpus(
        model,
        ['a', 'z', 'b'],
        ignore_failures=True,
    )

    assert reduced.inputs == ('a', 'b')
    assert reduced.failures == (1,)
    assert reduced.coverage.rules == {'start', 'word'}


def test_reduce_corpus_empty_input():
    model = compile_model()
    reduced = reduce_corpus(model, [])

    assert reduced.inputs == ()
    assert reduced.indices == ()
    assert reduced.failures == ()
    assert len(reduced.coverage) == 0
