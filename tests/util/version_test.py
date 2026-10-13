# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
from __future__ import annotations

import pytest

from tatsu.util.version import Version


def test_parse_prerelease_with_dotted_serial():
    v = Version.parse('5.24.1-b.1')
    assert (v.major, v.minor, v.micro) == (5, 24, 1)
    assert v.level == 'b'
    assert v.serial == 1


@pytest.mark.parametrize(
    ('text', 'level', 'serial'),
    [
        ('5.24.1', None, None),
        ('v5.24.1-b.1', 'b', 1),
        ('5.24.1b1', 'b', 1),
        ('5.24.1rc2', 'rc', 2),
        ('5.24.1-alpha.3', 'a', 3),
    ],
)
def test_parse_prerelease_spellings(text, level, serial):
    v = Version.parse(text)
    assert v.level == level
    assert v.serial == serial


@pytest.mark.parametrize('text', ['', 'junk', '5.24.1x'])
def test_parse_rejects_invalid(text):
    with pytest.raises(ValueError):  # noqa: PT011
        Version.parse(text)
