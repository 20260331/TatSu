# Copyright (c) 2017-2026 Juancarlo Añez (apalala@gmail.com)
# SPDX-License-Identifier: BSD-4-Clause
"""Relocatable bundles pairing a generated parser with its object model.

A *bundle* is a directory containing a generated parser module, a generated
object-model module, and a ``manifest.json`` describing the generation
identity (grammar name, grammar hash, TatSu version) together with content
checksums.  Both generated modules carry a matching bundle-identity marker,
so files produced by different generations cannot be mixed unnoticed.

Use :func:`export_bundle` to publish a bundle and :func:`load_bundle` to
verify and load one.  Every load uses fresh, uniquely named modules, so two
bundles generated from same-named grammars can coexist in a process without
sharing classes or state.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import shutil
import sys
import tempfile
import types
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from ._version import version as tatsu_version
from .exceptions import BundleError
from .ngcodegen.ngmodel_gen import modelgen
from .ngcodegen.ngparser_gen import pythongen
from .objectmodel import Node
from .objectmodel.builder import ModelBuilderSemantics
from .parsing import Parser
from .util import hasha


__all__ = [
    'BUNDLE_FORMAT',
    'BUNDLE_VERSION',
    'MANIFEST_FILENAME',
    'BundleError',
    'BundleInfo',
    'LoadedBundle',
    'export_bundle',
    'load_bundle',
    'verify_bundle',
]

BUNDLE_FORMAT = 'tatsu-parser-bundle'
BUNDLE_VERSION = 1
MANIFEST_FILENAME = 'manifest.json'
PARSER_FILENAME = 'parser.py'
MODEL_FILENAME = 'model.py'

_ROLES = ('parser', 'model')
_PATHS = {'parser': PARSER_FILENAME, 'model': MODEL_FILENAME}
_T = TypeVar('_T')
_MARKER_PREFIX = '# tatsu-bundle-'
_MARKER_BEGIN = '# >>> tatsu parser bundle (generated, do not edit)'
_MARKER_END = '# <<< tatsu parser bundle'


@dataclass(frozen=True, slots=True)
class BundleInfo:
    """Information about a bundle written by :func:`export_bundle`."""

    path: Path
    bundle_id: str
    grammar_name: str
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LoadedBundle:
    """A verified bundle together with its parser and model types.

    ``model_module`` holds the generated model classes, while ``semantics``
    is the generated ``ModelBuilderSemantics`` subclass to pass to the
    parser.  :meth:`parse` wires both together as a convenience.
    """

    path: Path
    bundle_id: str
    grammar_name: str
    manifest: dict[str, Any]
    parser_module: types.ModuleType
    model_module: types.ModuleType
    parser: type[Parser]
    semantics: type[ModelBuilderSemantics]

    def parse(self, text: str, /, **settings: Any) -> Any:
        """Parse ``text`` with the bundled parser and model semantics."""
        semantics = settings.pop('semantics', None)
        if semantics is None:
            semantics = self.semantics()
        return self.parser(semantics=semantics, **settings).parse(text)


def export_bundle(
    grammar: str,
    outdir: str | Path,
    /,
    *,
    name: str | None = None,
    filename: str | None = None,
    basetype: type = Node,
    config: Any = None,
    **settings: Any,
) -> BundleInfo:
    """Generate parser and object model, publishing them as a bundle.

    The bundle is assembled in a temporary sibling directory and verified
    before replacing ``outdir``.  A previously published bundle at
    ``outdir`` is left untouched if generation fails.
    """
    outdir = Path(outdir)

    # Local import to avoid an import cycle: tatsu.api imports this module.
    from .api.api import compile

    grammar_model = compile(
        grammar,
        name=name,
        filename=filename,
        config=config,
        **settings,
    )
    grammar_name = grammar_model.name

    parser_source = pythongen(grammar_model)
    model_source = modelgen(grammar_model, name=grammar_name, basetype=basetype)

    bundle_id = uuid.uuid4().hex
    payload = {
        'parser': _stamp(parser_source, bundle_id, grammar_name).encode('utf-8'),
        'model': _stamp(model_source, bundle_id, grammar_name).encode('utf-8'),
    }

    files = {
        role: {
            'path': _PATHS[role],
            'sha256': _sha256(payload[role]),
        }
        for role in _ROLES
    }

    manifest = {
        'format': BUNDLE_FORMAT,
        'bundle_version': BUNDLE_VERSION,
        'bundle_id': bundle_id,
        'grammar_name': grammar_name,
        'grammar_sha256': hasha(grammar),
        'tatsu_version': tatsu_version,
        'generated_at': datetime.now(UTC).isoformat(timespec='seconds'),
        'entries': {
            'parser_class': f'{grammar_name}Parser',
            'model_semantics_class': f'{grammar_name}ModelBuilderSemantics',
        },
        'files': files,
    }

    outdir.parent.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(
        tempfile.mkdtemp(
            dir=outdir.parent,
            prefix=f'.{outdir.name}.',
            suffix='.tmp',
        ),
    )
    try:
        for role in _ROLES:
            (tmpdir / _PATHS[role]).write_bytes(payload[role])
        (tmpdir / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        verify_bundle(tmpdir)
        _replace_directory(tmpdir, outdir)
    except BaseException:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise

    return BundleInfo(
        path=outdir,
        bundle_id=bundle_id,
        grammar_name=grammar_name,
        manifest=manifest,
    )


def verify_bundle(path: str | Path) -> dict[str, Any]:
    """Validate a bundle directory and return its manifest.

    Raises :class:`BundleError` for missing files, checksum mismatches
    (corruption), or files whose identity markers disagree with the
    manifest or each other (files from different generations mixed).
    """
    bundle_dir = Path(path)
    if not bundle_dir.is_dir():
        raise BundleError(f'not a bundle directory: {bundle_dir}')

    manifest_path = bundle_dir / MANIFEST_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except FileNotFoundError as e:
        raise BundleError(f'missing bundle manifest: {manifest_path}') from e
    except (OSError, json.JSONDecodeError) as e:
        raise BundleError(
            f'unreadable bundle manifest: {manifest_path}: {e}',
        ) from e

    if not isinstance(manifest, dict):
        raise BundleError('the bundle manifest must be a JSON object')
    if manifest.get('format') != BUNDLE_FORMAT:
        raise BundleError(f'not a TatSu parser bundle: {manifest_path}')

    bundle_version = manifest.get('bundle_version')
    if (
        not isinstance(bundle_version, int)
        or isinstance(bundle_version, bool)
        or not 0 < bundle_version <= BUNDLE_VERSION
    ):
        raise BundleError(f'unsupported bundle version: {bundle_version!r}')

    bundle_id = manifest.get('bundle_id')
    grammar_name = manifest.get('grammar_name')
    if not isinstance(bundle_id, str) or not bundle_id:
        raise BundleError('the bundle manifest is missing a valid "bundle_id"')
    if not isinstance(grammar_name, str) or not grammar_name:
        raise BundleError('the bundle manifest is missing a valid "grammar_name"')

    files = manifest.get('files')
    entries = manifest.get('entries')
    if not isinstance(files, dict) or not isinstance(entries, dict):
        raise BundleError('the bundle manifest has invalid "files" or "entries"')

    marker_ids: dict[str, str] = {}
    for role in _ROLES:
        spec = files.get(role)
        if not isinstance(spec, dict):
            raise BundleError(f'the bundle manifest is missing the "{role}" file')
        rel = spec.get('path')
        checksum = spec.get('sha256')
        if not isinstance(rel, str) or not isinstance(checksum, str):
            raise BundleError(f'the bundle manifest has an invalid "{role}" entry')

        member = _bundle_member(bundle_dir, rel)
        if not member.is_file():
            raise BundleError(f'missing bundle file: {rel}')

        data = member.read_bytes()
        if not hmac.compare_digest(_sha256(data), checksum):
            raise BundleError(
                f'checksum mismatch for {rel}: the file is corrupted or was '
                'replaced by one from a different generation',
            )

        source = data.decode('utf-8', errors='replace')
        marker_id = _read_marker(source, 'id')
        marker_grammar = _read_marker(source, 'grammar')
        if marker_id is None or marker_grammar is None:
            raise BundleError(
                f'{rel} has no bundle identity marker; it was likely produced '
                'by a plain (unbundled) generation',
            )
        if marker_id != bundle_id:
            raise BundleError(
                f'{rel} belongs to another generation '
                f'({marker_id[:8]} != {bundle_id[:8]}); refusing to mix bundles',
            )
        if marker_grammar != grammar_name:
            raise BundleError(
                f'{rel} was generated for grammar {marker_grammar!r}, but the '
                f'bundle manifest declares {grammar_name!r}',
            )
        marker_ids[role] = marker_id

    if marker_ids['parser'] != marker_ids['model']:
        raise BundleError(
            'the parser and the model come from different generations',
        )

    for key in ('parser_class', 'model_semantics_class'):
        if not isinstance(entries.get(key), str) or not entries[key]:
            raise BundleError(f'the bundle manifest is missing a valid "{key}"')

    return manifest


def load_bundle(path: str | Path) -> LoadedBundle:
    """Verify and load a bundle, returning matching parser and model types."""
    bundle_dir = Path(path)
    manifest = verify_bundle(bundle_dir)
    entries = manifest['entries']

    modules = {
        role: _load_isolated(
            _bundle_member(bundle_dir, manifest['files'][role]['path']),
            bundle_id=manifest['bundle_id'],
            role=role,
        )
        for role in _ROLES
    }

    parser_class = _resolve_class(
        modules['parser'],
        entries['parser_class'],
        Parser,
        role='parser',
    )
    semantics_class = _resolve_class(
        modules['model'],
        entries['model_semantics_class'],
        ModelBuilderSemantics,
        role='model semantics',
    )

    return LoadedBundle(
        path=bundle_dir,
        bundle_id=manifest['bundle_id'],
        grammar_name=manifest['grammar_name'],
        manifest=manifest,
        parser_module=modules['parser'],
        model_module=modules['model'],
        parser=parser_class,
        semantics=semantics_class,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stamp(source: str, bundle_id: str, grammar_name: str) -> str:
    marker = (
        f'\n\n{_MARKER_BEGIN}\n'
        f'{_MARKER_PREFIX}id: {bundle_id}\n'
        f'{_MARKER_PREFIX}grammar: {grammar_name}\n'
        f'{_MARKER_END}\n'
    )
    return source.rstrip() + marker


def _read_marker(source: str, field: str) -> str | None:
    prefix = f'{_MARKER_PREFIX}{field}:'
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def _bundle_member(bundle_dir: Path, rel: str) -> Path:
    member_path = Path(rel)
    if member_path.is_absolute() or '..' in member_path.parts:
        raise BundleError(f'unsafe path in bundle manifest: {rel}')
    root = bundle_dir.resolve()
    member = (bundle_dir / member_path).resolve()
    if not member.is_relative_to(root):
        raise BundleError(f'path escapes the bundle directory: {rel}')
    return member


def _replace_directory(src: Path, dst: Path) -> None:
    """Move directory ``src`` into the ``dst`` location, preserving ``dst``."""
    if not dst.exists():
        src.replace(dst)
        return

    backup = dst.with_name(dst.name + '.old')
    if backup.exists():
        shutil.rmtree(backup)
    dst.replace(backup)
    try:
        src.replace(dst)
    except OSError:
        # Put the previous bundle back before reporting the failure.
        backup.replace(dst)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _discard_bytecode_cache(*_args: Any, **_kwargs: Any) -> None:
    """Replacement for ``SourceFileLoader.set_data`` that writes nothing."""


def _load_isolated(path: Path, *, bundle_id: str, role: str) -> types.ModuleType:
    modname = f'_tatsu_bundle_{bundle_id[:12]}_{role}_{uuid.uuid4().hex[:12]}'
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise BundleError(f'cannot create an import specification for {path}')
    # Bundles are published artifacts: do not litter them with __pycache__.
    spec.loader.set_data = _discard_bytecode_cache  # type: ignore[attr-defined]
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(modname, None)
        raise
    return module


def _resolve_class(
    module: types.ModuleType,
    class_name: str,
    base: type[_T],
    *,
    role: str,
) -> type[_T]:
    found = getattr(module, class_name, None)
    if isinstance(found, type) and issubclass(found, base):
        return found

    for candidate in vars(module).values():
        if (
            isinstance(candidate, type)
            and issubclass(candidate, base)
            and candidate is not base
        ):
            return candidate

    raise BundleError(
        f'the {role} module does not define {class_name} '
        f'(a subclass of {base.__name__})',
    )
