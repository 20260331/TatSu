"""Reproducible demo for the TatSu q2 paired parser/model bundle feature.

Runs against the work-tree source (editable install) and prints one block per
Verify Rubric concern.  Run with the project virtualenv:

    .venv/bin/python evidence/demo_bundle.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import tatsu
from tatsu.exceptions import BundleError

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

# Deliberately the same grammar name, different structure/fields.
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


def section(n: str, title: str) -> None:
    print(f"\n[{n}] {title}")


def main() -> None:
    print(f"TatSu {tatsu.version}   python {sys.version.split()[0]}")

    root = Path(tempfile.mkdtemp(prefix='tatsu-q2-demo-'))
    outdir = root / 'calc'

    # 1. Legacy generation interfaces are unchanged -------------------------
    section('1', 'Legacy generation interfaces still work')
    parser_src = tatsu.to_python_sourcecode(GRAMMAR, name='Calc')
    model_src = tatsu.to_python_model(GRAMMAR, name='Calc')
    print(f"   to_python_sourcecode  -> {'class CalcParser(Parser):' in parser_src}")
    print(f"   to_python_model       -> {'class CalcModelBuilderSemantics' in model_src}")
    compiled = tatsu.compile(GRAMMAR, name='Calc')
    print(f"   compile(...).parse('1+2') -> {compiled.parse('1+2', parseinfo=False)}")

    # 2. Paired export publishes identity + checksums -----------------------
    section('2', 'Paired export publishes identity and content checksums')
    info = tatsu.export_bundle(GRAMMAR, outdir)
    print(f"   export_bundle -> bundle_id={info.bundle_id[:12]}... grammar={info.grammar_name}")
    print(f"   dir contents  -> {sorted(p.name for p in outdir.iterdir())}")
    manifest = json.loads((outdir / 'manifest.json').read_text())
    for key in ('format', 'bundle_version', 'bundle_id', 'grammar_name',
                'grammar_sha256', 'tatsu_version', 'generated_at'):
        value = manifest[key]
        value = value if not isinstance(value, str) or len(value) <= 24 else value[:21] + '...'
        print(f"   manifest.{key:16}= {value}")
    print(f"   manifest.entries = {manifest['entries']}")
    print(f"   manifest.files   = {sorted(manifest['files'])} each with sha256 + relative path")
    marker = f'# tatsu-bundle-id: {info.bundle_id}'
    print(f"   identity marker in parser.py and model.py -> "
          f"{marker in (outdir / 'parser.py').read_text() and marker in (outdir / 'model.py').read_text()}")

    # 3. Load returns a matching parser + model types -----------------------
    section('3', 'load_bundle returns parser and model types that parse together')
    loaded = tatsu.load_bundle(outdir)
    print(f"   parser     = {loaded.parser.__module__}.{loaded.parser.__name__}")
    print(f"   semantics  = {loaded.semantics.__name__}")
    result = loaded.parse('1+2')
    print(f"   parsed '1+2' -> {type(result).__name__}(left={result.left!r}, right={result.right!r}) "
          f"of bundled model -> {type(result) is loaded.model_module.Add}")
    parser = loaded.parser(semantics=loaded.semantics())
    print(f"   hand-wired parser.parse('7+8') -> {parser.parse('7+8', parseinfo=False)}")

    # 4. Relocatable --------------------------------------------------------
    section('4', 'Bundle is relocatable')
    moved = root / 'elsewhere' / 'calc'
    shutil.move(str(outdir), str(moved))
    relocated = tatsu.load_bundle(moved)
    r = relocated.parse('3+4')
    print(f"   moved to {moved.relative_to(root)} -> load OK, parse -> "
          f"{type(r).__name__}(left={r.left!r}, right={r.right!r})")

    # 5. Corrupted file rejected before executing code ----------------------
    section('5', 'Corrupted file is rejected before executing any code')
    tatsu.export_bundle(GRAMMAR, outdir)
    (outdir / 'parser.py').write_text((outdir / 'parser.py').read_text() + '\n# tampered\n')
    try:
        tatsu.load_bundle(outdir)
        print('   ERROR: corruption not rejected')
    except BundleError as e:
        print(f"   BundleError: {e}")

    # 6. Missing file rejected ---------------------------------------------
    section('6', 'Missing file is rejected')
    tatsu.export_bundle(GRAMMAR, outdir)
    (outdir / 'model.py').unlink()
    try:
        tatsu.load_bundle(outdir)
        print('   ERROR: missing file not rejected')
    except BundleError as e:
        print(f"   BundleError: {e}")

    # 7. Mixed generations rejected even after checksums are repaired ------
    section('7', 'Files from two generations are rejected, even after rehashing')
    first, other = root / 'first', root / 'other'
    tatsu.export_bundle(GRAMMAR, first)
    tatsu.export_bundle(GRAMMAR_OTHER, other)
    shutil.copyfile(other / 'model.py', first / 'model.py')
    manifest_path = first / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    import hashlib
    for spec in manifest['files'].values():
        spec['sha256'] = hashlib.sha256((first / spec['path']).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    try:
        tatsu.load_bundle(first)
        print('   ERROR: mixed generations not rejected')
    except BundleError as e:
        print(f"   BundleError: {e}")

    # 8. Failed generation preserves the previous directory ----------------
    section('8', 'Failed generation preserves the previous bundle')
    keep = root / 'keep'
    old = tatsu.export_bundle(GRAMMAR, keep)
    old_parser = (keep / 'parser.py').read_bytes()
    try:
        tatsu.export_bundle(BROKEN_GRAMMAR, keep)
    except Exception as e:  # noqa: BLE001 - demo
        print(f"   second export failed: {type(e).__name__}")
    same = tatsu.load_bundle(keep).bundle_id == old.bundle_id and (keep / 'parser.py').read_bytes() == old_parser
    leftovers = sorted(p.name for p in keep.parent.glob('.keep.*')) + [p.name for p in keep.parent.glob('keep.old')]
    print(f"   previous bundle intact -> {same}; temp/backup leftovers -> {leftovers}")

    # 9. Same-named grammars stay isolated ---------------------------------
    section('9', 'Two same-named grammars do not cross classes or state')
    dir_a, dir_b = root / 'a', root / 'b'
    tatsu.export_bundle(GRAMMAR, dir_a)
    tatsu.export_bundle(GRAMMAR_OTHER, dir_b)
    a = tatsu.load_bundle(dir_a)
    b = tatsu.load_bundle(dir_b)
    print(f"   both report grammar_name={a.grammar_name}; parser classes distinct -> {a.parser is not b.parser}")
    print(f"   model modules distinct -> {a.model_module is not b.model_module}; "
          f"model base classes distinct -> {a.model_module.ModelBase is not b.model_module.ModelBase}; "
          f"semantics distinct -> {a.semantics is not b.semantics}")
    ra, rb = a.parse('1+2'), b.parse('hello mars')
    print(f"   '1+2' -> {type(ra).__name__} (bundle A); 'hello mars' -> {type(rb).__name__} (bundle B)")
    try:
        a.parser().parse('hello venus')
        print('   ERROR: parser A accepted grammar B input')
    except Exception as e:  # noqa: BLE001
        print(f"   parser A rejects grammar B input -> {type(e).__name__}")

    shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
