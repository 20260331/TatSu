# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
from __future__ import annotations

import hashlib
import json
import shutil

import pytest

import tatsu
from tatsu.exceptions import BundleError, FailedParse, ParseException
from tatsu.objectmodel import Node
from tatsu.objectmodel.builder import ModelBuilderSemantics
from tatsu.parsing import Parser
from tatsu.util import hasha


GRAMMAR = r"""
    @@grammar :: Calc

    start = expression $ ;

    expression
        =
        | addition
        | number
        ;

    addition::Add
        =
        left:number '+' right:number
        ;

    number::int
        =
        /\d+/
        ;
"""

# A different grammar that deliberately keeps the same grammar name.
GRAMMAR_OTHER = r"""
    @@grammar :: Calc

    start = greeting $ ;

    greeting::Greet
        =
        'hello' who:word
        ;

    word
        =
        /[a-z]+/
        ;
"""

BROKEN_GRAMMAR = """
    @@grammar :: Broken

    start = rule_that_does_not_exist $ ;
"""


def _sha256_file(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rehash_manifest(bundle_dir) -> None:
    """Rewrite manifest checksums to match the files currently on disk."""
    manifest_path = bundle_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for spec in manifest['files'].values():
        spec['sha256'] = _sha256_file(bundle_dir / spec['path'])
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def test_export_creates_identity_and_checksum_manifest(tmp_path):
    outdir = tmp_path / 'calc'
    info = tatsu.export_bundle(GRAMMAR, outdir)

    assert info.path == outdir
    assert (outdir / 'parser.py').is_file()
    assert (outdir / 'model.py').is_file()
    assert (outdir / 'manifest.json').is_file()

    manifest = json.loads((outdir / 'manifest.json').read_text())
    assert manifest['format'] == 'tatsu-parser-bundle'
    assert manifest['bundle_version'] == 1
    assert manifest['bundle_id'] == info.bundle_id
    assert manifest['grammar_name'] == 'Calc'
    assert manifest['grammar_sha256'] == hasha(GRAMMAR)
    assert manifest['tatsu_version'] == tatsu.version
    assert manifest['entries'] == {
        'parser_class': 'CalcParser',
        'model_semantics_class': 'CalcModelBuilderSemantics',
    }

    for role, filename in (('parser', 'parser.py'), ('model', 'model.py')):
        spec = manifest['files'][role]
        assert spec['path'] == filename
        assert spec['sha256'] == _sha256_file(outdir / filename)

    parser_src = (outdir / 'parser.py').read_text()
    model_src = (outdir / 'model.py').read_text()
    assert f'# tatsu-bundle-id: {info.bundle_id}' in parser_src
    assert f'# tatsu-bundle-id: {info.bundle_id}' in model_src
    assert 'class CalcParser(Parser):' in parser_src
    assert 'class CalcModelBuilderSemantics' in model_src


def test_load_returns_matching_parser_and_model_types(tmp_path):
    bundle = tatsu.load_bundle(tatsu.export_bundle(GRAMMAR, tmp_path / 'calc').path)

    assert bundle.grammar_name == 'Calc'
    assert issubclass(bundle.parser, Parser)
    assert bundle.parser.__name__ == 'CalcParser'
    assert issubclass(bundle.semantics, ModelBuilderSemantics)
    assert bundle.semantics.__name__ == 'CalcModelBuilderSemantics'
    assert issubclass(bundle.model_module.Add, Node)

    result = bundle.parse('1+2')
    assert isinstance(result, bundle.model_module.Add)
    assert result.left == 1
    assert result.right == 2

    # The same pairing must work assembling parser and semantics by hand.
    parser = bundle.parser(semantics=bundle.semantics())
    result = parser.parse('7+8', parseinfo=False)
    assert isinstance(result, bundle.model_module.Add)
    assert (result.left, result.right) == (7, 8)


def test_bundle_is_relocatable(tmp_path):
    outdir = tmp_path / 'calc'
    tatsu.export_bundle(GRAMMAR, outdir)

    moved = tmp_path / 'elsewhere' / 'calc'
    shutil.move(str(outdir), str(moved))

    bundle = tatsu.load_bundle(moved)
    result = bundle.parse('3+4')
    assert isinstance(result, bundle.model_module.Add)
    assert (result.left, result.right) == (3, 4)


def test_corrupted_file_is_rejected(tmp_path):
    outdir = tmp_path / 'calc'
    tatsu.export_bundle(GRAMMAR, outdir)

    (outdir / 'parser.py').write_text(
        (outdir / 'parser.py').read_text() + '\n# tampered\n',
    )
    with pytest.raises(BundleError, match='checksum mismatch'):
        tatsu.load_bundle(outdir)


def test_missing_file_is_rejected(tmp_path):
    outdir = tmp_path / 'calc'
    tatsu.export_bundle(GRAMMAR, outdir)

    (outdir / 'model.py').unlink()
    with pytest.raises(BundleError, match='missing bundle file'):
        tatsu.load_bundle(outdir)


def test_broken_manifest_is_rejected(tmp_path):
    outdir = tmp_path / 'calc'
    tatsu.export_bundle(GRAMMAR, outdir)

    (outdir / 'manifest.json').write_text('{not json', encoding='utf-8')
    with pytest.raises(BundleError, match='unreadable bundle manifest'):
        tatsu.load_bundle(outdir)


def test_unsupported_bundle_version_is_rejected(tmp_path):
    outdir = tmp_path / 'calc'
    tatsu.export_bundle(GRAMMAR, outdir)

    manifest_path = outdir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['bundle_version'] = 99
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    with pytest.raises(BundleError, match='unsupported bundle version'):
        tatsu.load_bundle(outdir)


def test_manifest_path_escaping_is_rejected(tmp_path):
    outdir = tmp_path / 'calc'
    tatsu.export_bundle(GRAMMAR, outdir)

    manifest_path = outdir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files']['parser']['path'] = '../../escape.py'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    with pytest.raises(BundleError, match='unsafe path in bundle manifest'):
        tatsu.load_bundle(outdir)


def test_files_from_different_generations_are_rejected(tmp_path):
    first = tmp_path / 'first'
    other = tmp_path / 'other'
    tatsu.export_bundle(GRAMMAR, first)
    tatsu.export_bundle(GRAMMAR_OTHER, other)

    # Swap the model in, keeping the original (now stale) manifest.
    shutil.copyfile(other / 'model.py', first / 'model.py')
    with pytest.raises(BundleError, match='checksum mismatch'):
        tatsu.load_bundle(first)


def test_mixed_markers_rejected_even_if_checksums_repaired(tmp_path):
    first = tmp_path / 'first'
    other = tmp_path / 'other'
    tatsu.export_bundle(GRAMMAR, first)
    tatsu.export_bundle(GRAMMAR_OTHER, other)

    # An attacker/reshuffling user updates the checksums after swapping
    # files in; the generation-identity markers must still expose the mix.
    shutil.copyfile(other / 'model.py', first / 'model.py')
    _rehash_manifest(first)
    with pytest.raises(BundleError, match='another generation'):
        tatsu.load_bundle(first)


def test_unbundled_plain_generation_is_rejected(tmp_path):
    outdir = tmp_path / 'calc'
    info = tatsu.export_bundle(GRAMMAR, outdir)

    plain_parser = tatsu.to_python_sourcecode(GRAMMAR, name='Calc')
    (outdir / 'parser.py').write_text(plain_parser, encoding='utf-8')

    manifest_path = outdir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files']['parser']['sha256'] = _sha256_file(outdir / 'parser.py')
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    with pytest.raises(BundleError, match='identity marker'):
        tatsu.load_bundle(outdir)

    assert info.bundle_id  # the export itself remains valid information


def test_failed_export_preserves_previous_bundle(tmp_path):
    outdir = tmp_path / 'calc'
    first = tatsu.export_bundle(GRAMMAR, outdir)
    original_parser = (outdir / 'parser.py').read_text()

    with pytest.raises(ParseException):
        tatsu.export_bundle(BROKEN_GRAMMAR, outdir)

    assert not list(tmp_path.glob('.calc.*.tmp'))
    assert not (tmp_path / 'calc.old').exists()

    bundle = tatsu.load_bundle(outdir)
    assert bundle.bundle_id == first.bundle_id
    assert (outdir / 'parser.py').read_text() == original_parser
    result = bundle.parse('2+5')
    assert isinstance(result, bundle.model_module.Add)
    assert (result.left, result.right) == (2, 5)


def test_successful_reexport_replaces_bundle_without_leftovers(tmp_path):
    outdir = tmp_path / 'calc'
    first = tatsu.export_bundle(GRAMMAR, outdir)
    second = tatsu.export_bundle(GRAMMAR_OTHER, outdir)

    assert first.bundle_id != second.bundle_id
    assert not list(tmp_path.glob('.calc.*.tmp'))
    assert not (tmp_path / 'calc.old').exists()

    bundle = tatsu.load_bundle(outdir)
    assert bundle.bundle_id == second.bundle_id
    result = bundle.parse('hello world')
    assert isinstance(result, bundle.model_module.Greet)
    assert result.who == 'world'


def test_same_named_grammars_load_without_cross_state(tmp_path):
    dir_a = tmp_path / 'a' / 'calc'
    dir_b = tmp_path / 'b' / 'calc'
    tatsu.export_bundle(GRAMMAR, dir_a)
    tatsu.export_bundle(GRAMMAR_OTHER, dir_b)

    bundle_a = tatsu.load_bundle(dir_a)
    bundle_b = tatsu.load_bundle(dir_b)

    assert bundle_a.parser.__name__ == bundle_b.parser.__name__ == 'CalcParser'
    assert bundle_a.parser is not bundle_b.parser
    assert bundle_a.parser_module.__name__ != bundle_b.parser_module.__name__
    assert bundle_a.model_module is not bundle_b.model_module
    assert bundle_a.model_module.ModelBase is not bundle_b.model_module.ModelBase
    assert bundle_a.semantics is not bundle_b.semantics

    result_a = bundle_a.parse('1+2')
    result_b = bundle_b.parse('hello mars')
    assert isinstance(result_a, bundle_a.model_module.Add)
    assert isinstance(result_b, bundle_b.model_module.Greet)

    # Grammar A has no "greeting" rule; the two parsers must not share rules.
    with pytest.raises(FailedParse):
        bundle_a.parser().parse('hello venus')
    with pytest.raises(FailedParse):
        bundle_b.parser().parse('1+2')


def test_loading_does_not_write_bytecode_into_bundle(tmp_path):
    outdir = tmp_path / 'calc'
    tatsu.export_bundle(GRAMMAR, outdir)

    tatsu.load_bundle(outdir)
    tatsu.load_bundle(outdir)

    assert list(outdir.rglob('__pycache__')) == []


def test_loading_one_bundle_twice_yields_distinct_classes(tmp_path):
    outdir = tmp_path / 'calc'
    tatsu.export_bundle(GRAMMAR, outdir)

    first = tatsu.load_bundle(outdir)
    second = tatsu.load_bundle(outdir)

    assert first.parser is not second.parser
    assert first.parser_module is not second.parser_module
    assert first.model_module.Add is not second.model_module.Add
    assert first.semantics is not second.semantics

    assert isinstance(first.parse('1+2'), first.model_module.Add)
    assert isinstance(second.parse('3+4'), second.model_module.Add)


def test_legacy_generation_interfaces_remain_available():
    parser_source = tatsu.to_python_sourcecode(GRAMMAR, name='Calc')
    model_source = tatsu.to_python_model(GRAMMAR, name='Calc')

    assert 'class CalcParser(Parser):' in parser_source
    assert 'class CalcModelBuilderSemantics' in model_source

    model = tatsu.compile(GRAMMAR, name='Calc')
    assert model.parse('1+2', parseinfo=False) == {
        'left': '1',
        'right': '2',
    }
