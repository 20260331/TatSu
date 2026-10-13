# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
from __future__ import annotations

from typing import Any

from ...exceptions import OptionSucceeded
from ..ctx import Ctx, Func
from ._base import ContextBase


class ChoiceContext(ContextBase):
    def __init__(self, ctx: Ctx, choice_id: str = ''):
        super().__init__(ctx)
        self.choice_id = choice_id
        self.options: list[Func] = []
        self.result: Any = None

    def option(self, func: Func) -> Func:
        self.options.append(func)
        return func

    def parse(self, ctx: Ctx) -> Any:
        if not self.options:
            return None
        cov = ctx.coverage
        for option_index, opt in enumerate(self.options):
            try:
                with ctx.option():
                    ctx.expcall(opt)
            except OptionSucceeded:
                if cov is not None:
                    cov.branch_taken(self.choice_id, option_index)
                raise
        raise self.expectedexcept(ctx)
