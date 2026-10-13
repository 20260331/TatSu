# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
from __future__ import annotations

import builtins
import sys
import types

import pytest

import tatsu
from tatsu.api import compile, parse
from tatsu.exceptions import FailedParse, FailedSemantics, FailedToken
from tatsu.objectmodel import Node, synth
from tatsu.objectmodel.builder import (
    BuilderConfig,
    ModelBuilderSemantics,
    TypeResolutionError,
)


class MyNode:
    def __init__(self, _ast):
        pass


class BType:
    pass


class AType(BType):
    pass


def test_semantics_not_class():
    grammar = r"""
        start::sum = {number}+ $ ;
        number::int = /\d+/ ;
    """
    text = '5 4 3 2 1'
    bad_semantics = ModelBuilderSemantics  # NOTE: the class
    semantics = ModelBuilderSemantics()  # NOTE: the class

    with pytest.raises(
        TypeError,
        match=r'semantics must be an object instance or None.*',
    ):
        compile(grammar, semantics=bad_semantics)
        compile(grammar, semantics=bad_semantics)

    model = compile(grammar, 'test')
    with pytest.raises(
        TypeError,
        match=r'semantics must be an object instance or None.*',
    ):
        model.parse(text, semantics=bad_semantics)

    ast = model.parse(text, semantics=semantics)
    assert ast == 15


def test_builder_semantics():
    grammar = r"""
        start::sum = {number}+ $ ;
        number::int = /\d+/ ;
    """
    text = '5 4 3 2 1'

    semantics = ModelBuilderSemantics()
    model = compile(grammar, 'test')
    ast = model.parse(text, semantics=semantics)
    assert ast == 15

    import functools

    dotted = functools.partial(str.join, '.')
    # WARNING: this defeats all TatSu knows about functions and types
    #  dotted.__name__ = 'dotted'

    grammar = r"""
        start::dotted = {number}+ $ ;
        number = /\d+/ ;
    """

    semantics = ModelBuilderSemantics(constructors=[dotted])
    model = compile(grammar, 'test')
    with pytest.raises(
        TypeResolutionError,
        match=r"Could not find constructor for type 'dotted'",
    ):
        ast = model.parse(text, semantics=semantics)
        assert ast == '5.4.3.2.1'


def test_builder_subclassing():
    registry = getattr(synth, '__registry')

    grammar = """
        @@grammar :: Test
        start::A::B::C = $ ;
    """

    model = tatsu.compile_to_parser(grammar)
    model.parse('')

    print(f'{registry=}')
    A = registry['A']
    B = registry['B']
    C = registry['C']

    assert issubclass(A, B) and issubclass(A, synth.SynthNode) and issubclass(A, Node)
    assert issubclass(B, C) and issubclass(B, synth.SynthNode) and issubclass(A, Node)
    assert issubclass(C, synth.SynthNode) and issubclass(C, Node)


def test_builder_basetype_codegen():
    grammar = """
        @@grammar :: Test
        start::A::B::C = a:() b:() $ ;
        second::D::A = ();
        third = ();
    """

    from tatsu.api import to_python_model

    src = to_python_model(grammar, basetype=MyNode)
    # print(src[:1000])

    globals = {}
    exec(src, globals)  # pylint: disable=W0122
    semantics = globals['TestModelBuilderSemantics']()

    A = globals['A']
    B = globals['B']
    C = globals['C']
    D = globals['D']

    model = compile(grammar, semantics=semantics)
    ast = model.parse('', semantics=semantics)
    # print(f'AST({type(ast)}=', ast)

    assert isinstance(ast, MyNode), A.__mro__
    assert isinstance(ast, (A, B, C))
    assert hasattr(ast, 'a')
    assert hasattr(ast, 'b')

    assert issubclass(D, A | B | C)


def test_optional_attributes():
    grammar = r"""
        foo::Foo = left:identifier [ ':' right:identifier ] $ ;
        identifier = /\w+/ ;
    """

    grammar = compile(grammar)

    a = grammar.parse('foo : bar', semantics=ModelBuilderSemantics())
    assert a.left == 'foo'
    assert a.right == 'bar'

    b = grammar.parse('foo', semantics=ModelBuilderSemantics())
    assert b.left == 'foo'
    assert b.right is None


def test_constant_math():
    grammar = r"""
        start = a:`7` b:`2` @:```{a} / {b}``` $ ;
    """
    result = parse(grammar, '', trace=True)
    assert result <= 3.5 <= result


def test_constant_deep_eval():
    grammar = r"""
        start =
            a:A b:B
            @:```{a} / {b}```
            $
        ;

        A = number @:`7` ;
        B = number @:`0` ;
        number::int = /\d+/ ;
    """
    model = compile(grammar)

    with pytest.raises(
        FailedParse,
        match=r'Error evaluating constant.*ZeroDivisionError',
    ):
        # NOTE: only with multiple evaluation passes on constants
        model.parse('42 84', trace=True)


def test_builder_types():
    grammar = """
        @@grammar :: Test
        start::AType::BType = $ ;
    """

    builderconfig = BuilderConfig(basetype=BType, constructors=[AType, BType])
    ast = parse(grammar, '', builderconfig=builderconfig)
    assert type(ast) is AType
    assert isinstance(ast, BType)
    assert not isinstance(ast, synth.SynthNode)


def test_builder_nodedefs():
    grammar = """
        @@grammar :: Test
        start::AType::BType = $ ;
    """

    thismodule = sys.modules[__name__]
    builderconfig = BuilderConfig(typedefs=[thismodule], synthok=False)
    ast = parse(grammar, '', builderconfig=builderconfig)
    assert type(ast) is AType
    assert isinstance(ast, BType)
    assert not isinstance(ast, synth.SynthNode)


def test_ast_per_option():
    grammar = """
        start = options $ ;

        options =
            | a:'a' [b:'b']
            | c:'c' [d:'d']
            ;
    """

    # NOTE:
    #   Prove each option in a choide has its own version of the AST

    ast = parse(grammar, 'a b')
    assert ast == {'a': 'a', 'b': 'b'}
    assert 'c' not in ast
    assert 'd' not in ast

    ast = parse(grammar, 'c d')
    assert ast == {'c': 'c', 'd': 'd'}
    assert 'a' not in ast
    assert 'b' not in ast

    ast = parse(grammar, 'a')
    assert ast == {'a': 'a', 'b': None}
    assert 'b' in ast
    assert 'c' not in ast
    assert 'd' not in ast

    ast = parse(grammar, 'c')
    assert ast == {'c': 'c', 'd': None}
    assert 'd' in ast
    assert 'a' not in ast
    assert 'b' not in ast


def test_ast_names_accumulate():
    grammar = """
        start: options $

        options:
            | a='a' ([b='b'] {x='x'})
            | c='c' ([d='d'] y={'y'})
            | w={} f='f'
            | g=() g='g'
            | h={} h='h'
            | i+='i'
            | [j+='j'] k='k'
    """

    # NOTE:
    #   Prove named elements accumulat
    ast = parse(grammar, 'a')
    assert ast == {'a': 'a', 'b': None, 'x': None}

    ast = parse(grammar, 'a x')
    assert ast == {'a': 'a', 'b': None, 'x': 'x'}

    ast = parse(grammar, 'a x x')
    assert ast == {'a': 'a', 'b': None, 'x': ['x', 'x']}

    # NOTE:
    #   Prove naming closures always closure
    ast = parse(grammar, 'c')
    assert ast == {'c': 'c', 'd': None, 'y': []}

    ast = parse(grammar, 'c y')
    assert ast == {'c': 'c', 'd': None, 'y': ['y']}

    ast = parse(grammar, 'c y y')
    assert ast == {'c': 'c', 'd': None, 'y': ['y', 'y']}

    ast = parse(grammar, 'f')
    assert ast == {'f': 'f', 'w': []}

    ast = parse(grammar, 'g')
    assert ast == {'g': [(), 'g']}

    ast = parse(grammar, 'h')
    assert ast == {'h': [[], 'h']}

    ast = parse(grammar, 'i')
    assert ast == {'i': ['i']}

    ast = parse(grammar, 'k')
    assert ast == {'j': [], 'k': 'k'}


def test_cut_scope():
    grammar = """
        start = (failcut | failchoice | succeed) $ ;

        failcut = 'a' ~ 'y' ;

        failchoice =
            | 'a' ~ 'b'
            | 'a' 'c' 'd'
            ;

        succeed = ('a' ~ 'y' | 'b' 'z') | 'a' 'x' ;
    """

    ast = parse(grammar, 'a x')
    assert ast == ['a', 'x']

    ast = parse(grammar, 'a b')
    assert ast == ['a', 'b']

    ast = parse(grammar, 'a y')
    assert ast == ['a', 'y']

    with pytest.raises(FailedToken, match=r"expecting 'b'"):
        ast = parse(grammar, 'a c d')
        assert ast == ['a', 'c', 'd']


# --- Deferred (committed) semantic actions ---------------------------------


def _generated_parser(grammar):
    code = tatsu.to_python_sourcecode(grammar, name='DeferredTest')
    module = types.ModuleType('test_deferred_generated')
    module.__file__ = '<generated>'
    exec(  # noqa: S102
        builtins.compile(code, module.__file__, 'exec'),
        module.__dict__,
    )
    return module.DeferredTestParser()


class _Recorder:
    """Records both immediate action effects and deferred commits."""

    def __init__(self):
        self.immediate: list[str] = []
        self.committed: list[str] = []
        self.ctx = None

    def set_context(self, ctx):
        self.ctx = ctx

    def _note(self, label: str):
        self.immediate.append(label)
        assert self.ctx is not None
        self.ctx.defer(self.committed.append, label)


ORDER_GRAMMAR = r"""
    start = a b $ ;
    a = 'a' ;
    b = 'b' ;
"""


class OrderSemantics(_Recorder):
    def a(self, ast):
        self._note('a')
        return ast

    def b(self, ast):
        self._note('b')
        return ast


@pytest.mark.parametrize('generated', [False, True])
def test_deferred_commit_after_success_in_order(generated):
    parser = (
        _generated_parser(ORDER_GRAMMAR)
        if generated
        else compile(ORDER_GRAMMAR, 'DeferredTest')
    )
    semantics = OrderSemantics()

    result = parser.parse('a b', semantics=semantics)
    assert result == ['a', 'b']

    # actions run while parsing; deferred callbacks after the parse, in order
    assert semantics.immediate == ['a', 'b']
    assert semantics.committed == ['a', 'b']


FAILED_CHOICE_GRAMMAR = r"""
    start = choice $ ;
    choice = probe 'x' | 'g' ;
    probe = 'g' ;
"""


class FailedChoiceSemantics(_Recorder):
    def probe(self, ast):
        self._note('probe')
        return ast


@pytest.mark.parametrize('generated', [False, True])
def test_deferred_dropped_for_failed_choice(generated):
    parser = (
        _generated_parser(FAILED_CHOICE_GRAMMAR)
        if generated
        else compile(FAILED_CHOICE_GRAMMAR, 'DeferredTest')
    )
    semantics = FailedChoiceSemantics()

    parser.parse('g', semantics=semantics)

    # the action ran while trying the abandoned option,
    # but its deferred callback is never committed
    assert semantics.immediate == ['probe']
    assert semantics.committed == []


LOOKAHEAD_GRAMMAR = r"""
    start = &probe 'g' $ ;
    probe = 'g' ;
"""


class LookaheadSemantics(_Recorder):
    def probe(self, ast):
        self._note('probe')
        return ast


@pytest.mark.parametrize('generated', [False, True])
def test_deferred_dropped_for_lookahead(generated):
    parser = (
        _generated_parser(LOOKAHEAD_GRAMMAR)
        if generated
        else compile(LOOKAHEAD_GRAMMAR, 'DeferredTest')
    )
    semantics = LookaheadSemantics()

    parser.parse('g', semantics=semantics)

    assert semantics.immediate == ['probe']
    assert semantics.committed == []


MEMO_GRAMMAR = r"""
    start = choice $ ;
    choice = probe 'x' | probe 'g' ;
    probe = 'g' ;
"""


class MemoSemantics(_Recorder):
    def probe(self, ast):
        self._note('probe')
        return ast


@pytest.mark.parametrize('generated', [False, True])
def test_deferred_readopted_from_memo(generated):
    parser = (
        _generated_parser(MEMO_GRAMMAR)
        if generated
        else compile(MEMO_GRAMMAR, 'DeferredTest')
    )
    semantics = MemoSemantics()

    # NOTE: whitespace separates the tokens ('g' is a token name)
    parser.parse('g g', semantics=semantics)

    # the action ran only once, but the memoized result is adopted by the
    # winning alternative, so its callback commits exactly once
    assert semantics.immediate == ['probe']
    assert semantics.committed == ['probe']


CLOSURE_GRAMMAR = r"""
    start = {item}+ $ ;
    item = /\d/ ;
"""


class ClosureSemantics(_Recorder):
    def item(self, ast):
        self._note(ast)
        return ast


@pytest.mark.parametrize('generated', [False, True])
def test_deferred_committed_for_every_closure_iteration(generated):
    parser = (
        _generated_parser(CLOSURE_GRAMMAR)
        if generated
        else compile(CLOSURE_GRAMMAR, 'DeferredTest')
    )
    semantics = ClosureSemantics()

    parser.parse('123', semantics=semantics)

    assert semantics.immediate == ['1', '2', '3']
    assert semantics.committed == ['1', '2', '3']


LEFTREC_GRAMMAR = r"""
    start = e $ ;
    e = e '+' n | n ;
    n = /\d/ ;
"""


class LeftRecSemantics(_Recorder):
    def n(self, ast):
        self._note(f'n:{ast}')
        return ast

    def e(self, ast):
        label = 'e:base' if ast == '1' else 'e:grow'
        self._note(label)
        return ast


@pytest.mark.parametrize('generated', [False, True])
def test_deferred_leftrecursion_intermediates(generated):
    parser = (
        _generated_parser(LEFTREC_GRAMMAR)
        if generated
        else compile(LEFTREC_GRAMMAR, 'DeferredTest')
    )
    semantics = LeftRecSemantics()

    parser.parse('1+2', semantics=semantics)

    # the semantic action runs on every left-recursion growth step,
    # including the final attempt that does not extend the match;
    # its deferred callback is the one that gets discarded
    assert semantics.immediate == [
        'n:1',
        'e:base',
        'n:2',
        'e:grow',
        'e:base',
    ]
    # only callbacks of the adopted derivation commit,
    # in derivation order and without duplicates
    assert semantics.committed == ['n:1', 'e:base', 'n:2', 'e:grow']


FAIL_GRAMMAR = r"""
    start = a b $ ;
    a = 'a' ;
    b = 'b' ;
"""


class FailSemantics(_Recorder):
    def a(self, ast):
        self._note('a')
        return ast

    def b(self, ast):
        self._note('b')
        return ast


def test_deferred_rolled_back_when_parse_fails():
    model = compile(FAIL_GRAMMAR, 'DeferredTest')
    semantics = FailSemantics()

    with pytest.raises(FailedParse):
        model.parse('a x', semantics=semantics)

    assert semantics.immediate == ['a']
    assert semantics.committed == []


class FailedSemanticsSemantics(_Recorder):
    def a(self, _ast):
        raise FailedSemantics('no a')

    def b(self, ast):
        self._note('b')
        return ast


def test_failed_semantics_does_not_commit():
    model = compile(FAIL_GRAMMAR, 'DeferredTest')
    semantics = FailedSemanticsSemantics()

    with pytest.raises(FailedParse):
        model.parse('a b', semantics=semantics)

    assert semantics.committed == []


class RaisingDeferredSemantics(FailSemantics):
    def a(self, ast):
        self.immediate.append('a')
        self.ctx.defer(self._boom)
        return ast

    def _boom(self):
        raise RuntimeError('commit failed')


def test_deferred_callback_exception_propagates():
    model = compile(FAIL_GRAMMAR, 'DeferredTest')
    semantics = RaisingDeferredSemantics()

    with pytest.raises(RuntimeError, match='commit failed'):
        model.parse('a b', semantics=semantics)

    assert semantics.immediate == ['a', 'b']
    assert semantics.committed == []


def test_deferred_reset_between_parses():
    model = compile(ORDER_GRAMMAR, 'DeferredTest')
    semantics = OrderSemantics()

    model.parse('a b', semantics=semantics)
    assert semantics.committed == ['a', 'b']

    with pytest.raises(FailedParse):
        model.parse('a x', semantics=semantics)

    # the failed second parse must not leave callbacks behind
    assert semantics.committed == ['a', 'b']


if __name__ == '__main__':
    test_cut_scope()
