# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
from __future__ import annotations

import pytest

from tatsu.util import Version


@pytest.mark.parametrize(
    'text',
    ['v5.24.1-b.1', '5.24.1b.1', '5.24.1-b-1', '5.24.1_b_1', '5.24.1b1'],
)
def test_prerelease_separators_between_letter_and_number(text):
    # PEP 440 allows a separator between the pre-release letter and its number
    version = Version.parse(text)
    assert version.level == 'b'
    assert version.serial == 1


def test_release_version_still_parses():
    version = Version.parse('v5.24.0')
    assert (version.major, version.minor, version.micro) == (5, 24, 0)
    assert version.level is None


def test_invalid_version_string_is_rejected():
    with pytest.raises(ValueError, match='Invalid version string'):
        Version.parse('not-a-version')
