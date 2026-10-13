# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
from __future__ import annotations

import re
import unittest

from tatsu import compile, generate
from tatsu.generation import SampleGenerator
from tatsu.generation.regexgen import RegexGenError, regex_samples
from tatsu.generation.samples import GenerationError


EXPR_GRAMMAR = r"""
    start = expr $ ;
    expr = term ('+' term)* ;
    term = /\d+/ | '(' expr ')' ;
"""


class RegexSamplingTests(unittest.TestCase):
    def test_samples_match_patterns(self):
        for pattern in (
            r'\d+',
            r'[a-zA-Z_]\w*',
            r'-?\d+(\.\d+)?',
            r'(ab|c)+',
            r'x{2,3}',
            r'\s*',
            r'https?://',
        ):
            compiled = re.compile(pattern)
            for sample in regex_samples(pattern):
                self.assertIsNotNone(
                    compiled.fullmatch(sample),
                    msg=f'{sample!r} does not match {pattern!r}',
                )

    def test_unsupported_construct(self):
        with self.assertRaises(RegexGenError):
            regex_samples(r'(?=x)')


class SampleGenerationTests(unittest.TestCase):
    def _assert_legal(self, grammar_source, samples, *, start=None):
        model = compile(grammar_source)
        for sample in samples:
            model.parse(sample, start=start)

    def test_tokens_choice_and_optional(self):
        grammar = "start = 'a' ['b'] 'c' $ ;"
        samples = generate(grammar, count=4)
        self.assertEqual(['a c', 'a b c'], samples)
        self._assert_legal(grammar, samples)

    def test_patterns_closures_and_join(self):
        grammar = r"""
            start = /[a-z]+/ ','%{/\d+/} $ ;
        """
        samples = generate(grammar, count=15, max_depth=2)
        self._assert_legal(grammar, samples)
        self.assertIn('a0,0', samples)
        self.assertTrue(any(',' in s for s in samples))

    def test_recursion_is_bounded_by_depth(self):
        shallow = generate(EXPR_GRAMMAR, count=20, max_depth=1)
        self._assert_legal(EXPR_GRAMMAR, shallow)
        self.assertFalse(any('(' in s for s in shallow))

        deeper = generate(EXPR_GRAMMAR, count=40, max_depth=2)
        self._assert_legal(EXPR_GRAMMAR, deeper)
        self.assertTrue(any('(' in s for s in deeper))

    def test_left_recursion(self):
        grammar = r"""
            start = a $ ;
            a = a 'x' | 'y' ;
        """
        samples = generate(grammar, count=4, max_depth=3)
        self.assertEqual(['y', 'y x', 'y x x', 'y x x x'], samples)
        self._assert_legal(grammar, samples)

    def test_mutual_recursion(self):
        grammar = r"""
            start = a $ ;
            a = 'x' b? ;
            b = 'y' a? ;
        """
        samples = generate(grammar, count=5, max_depth=3)
        self._assert_legal(grammar, samples)
        self.assertEqual(['x', 'x y', 'x y x', 'x y x y', 'x y x y x'], samples)

    def test_empty_and_nullable_rules(self):
        grammar = r"""
            start = maybe $ ;
            maybe = 'x'? ;
        """
        samples = generate(grammar)
        self.assertIn('', samples)
        self._assert_legal(grammar, samples)

        grammar = r"""
            start = empty 'x' $ ;
            empty = () ;
        """
        samples = generate(grammar)
        self.assertEqual(['x'], samples)

    def test_positive_and_negative_lookahead(self):
        grammar = r"""
            start = &/\d/ /\d+/ '!' $ ;
        """
        samples = generate(grammar, count=3)
        self._assert_legal(grammar, samples)
        self.assertEqual(['0!', '1!', '2!'], samples)

        grammar = r"""
            start = !'bad' /[a-z]+/ /\d+/ $ ;
        """
        samples = generate(grammar, count=3)
        self._assert_legal(grammar, samples)
        for sample in samples:
            self.assertFalse(sample.startswith('bad'))

    def test_skips_to(self):
        grammar = r"""
            start = 'x' ->'end' $ ;
        """
        samples = generate(grammar, count=3)
        self._assert_legal(grammar, samples)
        self.assertIn('x end', samples)

    def test_meta_matchers(self):
        grammar = r"""
            start = @int '+' @float '+' @bool '+' @name $ ;
        """
        samples = generate(grammar, count=2)
        self._assert_legal(grammar, samples)

    def test_no_whitespace_directive(self):
        grammar = r"""
            @@whitespace :: None

            start = /\d+/ '!' $ ;
        """
        samples = generate(grammar, count=3)
        self.assertEqual(['0!', '1!', '2!'], samples)
        self._assert_legal(grammar, samples)

    def test_count_budget(self):
        grammar = "start = 'a' | 'b' | 'c' | 'd' $ ;"
        self.assertEqual(2, len(generate(grammar, count=2)))
        self.assertEqual(4, len(generate(grammar, count=10)))

    def test_deterministic(self):
        grammar = "start = 'a' ['b'] 'c'+ $ ;"
        first = generate(grammar, count=5, max_depth=2, attempts=100)
        second = generate(grammar, count=5, max_depth=2, attempts=100)
        self.assertEqual(first, second)
        self._assert_legal(grammar, first)

    def test_impossible_grammar_raises(self):
        grammar = r"""
            start = &'a' 'b' $ ;
        """
        with self.assertRaises(GenerationError):
            generate(grammar, count=1, max_depth=2, attempts=50)

    def test_invalid_parameters(self):
        model = compile("start = 'a' $ ;")
        with self.assertRaises(ValueError):
            model.generate_samples(count=0)
        with self.assertRaises(ValueError):
            model.generate_samples(max_depth=-1)
        with self.assertRaises(KeyError):
            SampleGenerator(model, start='missing')

    def test_user_semantics_never_executed(self):
        class Semantics:
            def __init__(self):
                self.calls = 0

            def start(self, ast):
                self.calls += 1
                return ast

            def _default(self, ast):
                self.calls += 1
                return ast

        semantics = Semantics()
        model = compile(EXPR_GRAMMAR, semantics=semantics)
        samples = model.generate_samples(count=5, max_depth=2)
        self.assertEqual(0, semantics.calls)
        self._assert_legal(EXPR_GRAMMAR, samples)

        # the samples are still accepted when semantics are enabled
        model.parse(samples[0])
        self.assertGreater(semantics.calls, 0)

    def test_sample_generator_and_module_function(self):
        model = compile("start = 'y' | 'n' $ ;")
        samples = SampleGenerator(model).generate(count=2)
        self.assertEqual(['n', 'y'], samples)

        from tatsu.generation import generate as module_generate

        self.assertEqual(
            samples,
            module_generate(model, count=2),
        )

    def test_examples_from_realistic_grammar(self):
        grammar = r"""
            start = value $ ;
            value
                = /\d+/
                | '"' /[a-z]+/ '"'
                | '[' [value (',' value)*] ']'
                ;
        """
        samples = generate(grammar, count=14, max_depth=3)
        self._assert_legal(grammar, samples)
        for expected in ('0', '[]', '"a"', '[0]'):
            self.assertIn(expected, samples)
        self.assertTrue(any(s.startswith('[0,') for s in samples))


if __name__ == '__main__':
    unittest.main()
