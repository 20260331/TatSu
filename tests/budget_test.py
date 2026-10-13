# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
from __future__ import annotations

import pickle

import pytest

import tatsu
from tatsu.config import ParserConfig
from tatsu.exceptions import (
    BUDGET_MEMO_ENTRIES,
    BUDGET_RECURSION_DEPTH,
    BUDGET_RULE_CALLS,
    BudgetExceeded,
    FailedParse,
    ParseException,
)


GRAMMAR_ITEMS = r'''
    @@grammar :: BudgetItems
    start = {item}+ $ ;
    item = /\d/ ;
'''

GRAMMAR_RECURSION = r'''
    @@grammar :: BudgetRecursion
    start = /x/ start | /x/ ;
'''

GRAMMAR_IN_OPTIONAL = r'''
    @@grammar :: BudgetOptional
    start = [bad] ;
    bad = /x/ {more}+ ;
    more = /x/ ;
'''

GRAMMAR_IN_CHOICE = r'''
    @@grammar :: BudgetChoice
    start = a | b ;
    a = /x/ {more}+ ;
    more = /x/ ;
    b = /z/ ;
'''

GRAMMAR_IN_LOOKAHEAD = r'''
    @@grammar :: BudgetLookahead
    start = !bad /z/ ;
    bad = /x/ {more}+ ;
    more = /x/ ;
'''

GRAMMAR_IN_RECOVERY = r'''
    @@grammar :: BudgetRecovery
    start = '(' ->end ;
    end = 'b' ;
'''


@pytest.fixture(scope='module')
def model_items():
    return tatsu.compile(GRAMMAR_ITEMS)


@pytest.fixture(scope='module')
def model_recursion():
    return tatsu.compile(GRAMMAR_RECURSION)


def test_unlimited_budget_parses_normally():
    model = tatsu.compile(GRAMMAR_ITEMS)
    assert model.parse('1234567890') == list('1234567890')
    assert model.parse('1234567890', budget=None) == list('1234567890')


def test_budget_limits_rule_calls(model_items):
    with pytest.raises(BudgetExceeded) as info:
        model_items.parse('1234567890', budget=3, memoization=False)

    e = info.value
    assert e.resource == BUDGET_RULE_CALLS
    assert e.budget == 3
    assert e.used == 4
    assert e.rule == 'item'
    # the error is raised before the offending rule consumes input
    assert e.pos == 2
    assert isinstance(e.pos, int)


def test_budget_limits_recursion_depth(model_recursion):
    with pytest.raises(BudgetExceeded) as info:
        model_recursion.parse('xxxxxxxxx', budget=5)

    e = info.value
    assert e.resource == BUDGET_RECURSION_DEPTH
    assert e.budget == 5
    assert e.used == 6
    assert e.rule == 'start'
    # raised at the 6th level, before the 6th token is consumed
    assert e.pos == 5


def test_depth_accounting_survives_backtracking(model_recursion):
    # a sufficient budget must survive the failure-and-unwind at the
    # end of the input (depth is released on every failed branch)
    assert model_recursion.parse('xxxxxxxxx', budget=20) is not None


def test_depth_budget_precedes_python_recursion_error(model_recursion):
    with pytest.raises(BudgetExceeded) as info:
        model_recursion.parse('x' * 10_000, budget=100)
    assert info.value.resource == BUDGET_RECURSION_DEPTH


def test_budget_error_is_picklable(model_recursion):
    with pytest.raises(BudgetExceeded) as info:
        model_recursion.parse('xxxxxxxxx', budget=2)

    e = info.value
    restored = pickle.loads(pickle.dumps(e))
    assert isinstance(restored, BudgetExceeded)
    assert restored.resource == e.resource
    assert restored.budget == e.budget
    assert restored.used == e.used
    assert restored.pos == e.pos
    assert restored.rule == e.rule


def test_budget_limits_memo_entries(model_items):
    with pytest.raises(BudgetExceeded) as info:
        model_items.parse('1234567890', budget=3)

    e = info.value
    assert e.resource == BUDGET_MEMO_ENTRIES
    assert e.used > e.budget
    assert e.rule == 'item'


def test_budget_error_is_fatal_not_a_parse_failure():
    assert not issubclass(BudgetExceeded, ParseException)
    assert not issubclass(BudgetExceeded, FailedParse)


def test_budget_error_is_locatable(model_items):
    with pytest.raises(BudgetExceeded) as info:
        model_items.parse('1234567890', budget=3, memoization=False)

    e = info.value
    rendered = e.render()
    assert 'budget exceeded' in rendered
    assert 'item' in rendered
    assert str(e) == rendered


def test_budget_error_not_swallowed_by_optional():
    model = tatsu.compile(GRAMMAR_IN_OPTIONAL)
    with pytest.raises(BudgetExceeded) as info:
        model.parse('xxxxxx', budget=2, memoization=False)
    assert info.value.rule == 'more'


def test_budget_error_not_swallowed_by_choice():
    model = tatsu.compile(GRAMMAR_IN_CHOICE)
    with pytest.raises(BudgetExceeded) as info:
        model.parse('xxxxxx', budget=2, memoization=False)
    # option a exhausted the budget; option b must not be attempted
    assert info.value.rule == 'more'


def test_budget_error_not_swallowed_by_negative_lookahead():
    model = tatsu.compile(GRAMMAR_IN_LOOKAHEAD)
    with pytest.raises(BudgetExceeded) as info:
        model.parse('xxxxxx', budget=2, memoization=False)
    assert info.value.rule == 'more'


def test_budget_error_not_swallowed_by_error_recovery():
    model = tatsu.compile(GRAMMAR_IN_RECOVERY)
    with pytest.raises(BudgetExceeded) as info:
        model.parse('(' + 'a' * 12, budget=2, memoization=False)
    assert info.value.resource == BUDGET_RULE_CALLS
    assert info.value.rule == 'end'


def test_budget_is_reset_between_parses(model_items):
    text = '1234567890'

    with pytest.raises(BudgetExceeded):
        model_items.parse(text, budget=2, memoization=False)

    # an unlimited parse on the same parser instance works afterwards
    assert model_items.parse(text) == list('1234567890')

    # and the budget applies again on the next parse
    with pytest.raises(BudgetExceeded):
        model_items.parse(text, budget=2, memoization=False)


def test_budget_from_parser_config(model_items):
    config = ParserConfig(budget=2, memoization=False)
    with pytest.raises(BudgetExceeded) as info:
        model_items.parse('1234567890', config=config)
    assert info.value.resource == BUDGET_RULE_CALLS


def test_high_level_parse_budgets_text_not_grammar_compilation():
    # a small budget must not abort the internal parse that compiles
    # the grammar, only the parse of the input text
    with pytest.raises(BudgetExceeded) as info:
        tatsu.parse(GRAMMAR_ITEMS, '1234567890', budget=3, memoization=False)
    assert info.value.resource == BUDGET_RULE_CALLS


def test_budget_config_validation():
    assert ParserConfig().budget is None
    assert ParserConfig(budget=42).budget == 42

    for invalid in (0, -1, 1.5, True, '10'):
        with pytest.raises(ValueError, match='budget'):
            ParserConfig(budget=invalid)
