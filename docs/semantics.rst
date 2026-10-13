.. Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
.. SPDX-License-Identifier: BSD-4-Clause

.. include:: links.rst

Semantic Actions
----------------

|TatSu| grammars lack notation for semantic actions. That is on purpose,
because semantic actions within a grammar obscure the declarative nature
of the context, and allow poor modularization from the parser-execution
perspective.

Semantic actions are defined in a class, and applied by passing an
object of the class to the ``parse()`` method of the parser as the
``semantics=`` parameter. |TatSu| will invoke the method that matches
the name of the grammar rule every time the rule parses. The argument to
the method will be the `AST`_ constructed from the right-hand-side of
the rule:

.. code:: python

    class MySemantics:
        def some_rule_name(self, ast):
            return ''.join(ast)

        def _default(self, ast):
            pass

If there's no method matching the rule's name, |TatSu| will try to
invoke a ``_default()`` method if it's defined:

.. code:: python

    def _default(self, ast):
        ...

Nothing will happen if neither the per-rule method nor ``_default()``
are defined.

The per-rule methods in classes implementing the semantics provide
enough opportunity to do rule post-processing operations, like
verifications (for inadequate use of keywords as identifiers), or `AST`_
transformation:

.. code:: python

    class MyLanguageSemantics:
        def identifier(self, ast):
            if my_lange_module.is_keyword(ast):
                raise FailedSemantics('"%s" is a keyword' % str(ast))
            return ast

For finer-grained control it's enough to declare more rules, as the
impact on the parsing times will be minimal.

If preprocessing is required at some point, it's enough to place
invocations of empty rules where appropriate:

.. code:: ebnf
   :force:

    myrule: first_part preproc {second_part}

    preproc: ()

The abstract parser will honor as a semantic action a method declared
as:

.. code:: python

    def preproc(self, ast):
        ...

Deferred Actions
~~~~~~~~~~~~~~~~

Semantic actions run as soon as their rule matches, even while the
parser is trying alternatives that fail later, while evaluating
lookahead, or on the intermediate results of left-recursion growth.
Actions with external side effects (appending to a list, emitting code,
updating a symbol table) may therefore observe attempts that do not
belong to the final parse.

For those effects, the parse context provides ``defer()``. A callback
registered with ``defer()`` is held until the whole parse succeeds and
the derivation that registered it is the one adopted. The callbacks
then run, in registration order, after parsing finishes and before
``parse()`` returns:

.. code:: python

    class MySemantics:
        def set_context(self, ctx):
            self.ctx = ctx

        def some_rule_name(self, ast):
            self.ctx.defer(self.commit_result, ast)
            return ast

        def commit_result(self, ast):
            ...  # runs only if this derivation is adopted

Call ``defer(callback, *args, **kwargs)`` to have arguments bound to
the callback. Callbacks registered while parsing an alternative that
fails, inside syntactic (``&``) or negative (``!``) lookahead, or on a
left-recursion result that is later extended, are discarded. If the
parse fails, no deferred callbacks run. A result that is reused from the
packrat memo by an adopted derivation commits its callbacks exactly
once.

The semantics object receives the parse context through the standard
``set_context()`` method. Deferred actions behave identically with a
compiled grammar model and with a generated parser, as both share the
same parse engine.


.. _Abstract Syntax Tree: http://en.wikipedia.org/wiki/Abstract_syntax_tree
.. _AST: http://en.wikipedia.org/wiki/Abstract_syntax_tree
.. _PEG: http://en.wikipedia.org/wiki/Parsing_expression_grammar
.. _Python: http://python.org
.. _keywords: https://en.wikipedia.org/wiki/Reserved_word
