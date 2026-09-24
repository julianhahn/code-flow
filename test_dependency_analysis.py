"""Real compiler fixtures, plus process and clone safety checks. No package installs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from DependencyAnalysis import DependencyAnalysis


class RepositoryFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='code-flow-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve() / 'review'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        self.write('.gitignore', 'node_modules/\n')
        self.write('package.json', '{}')
        self.write('tsconfig.json', json.dumps({'compilerOptions': {
            'strict': True, 'target': 'ES2020', 'module': 'ESNext', 'moduleResolution': 'bundler',
            'paths': {'@lib/*': ['./src/*']}}, 'include': ['src/**/*.ts']}))
        self.write('src/a.ts', 'export function greet() { return 1; }\nexport interface Shape { value: number }\n')
        self.write('src/barrel.ts', "export { greet, type Shape } from './a';\n")
        self.write('src/b.ts', "import {greet as hello} from '@lib/barrel';\nimport type {Shape} from './a';\nhello(); hello();\nexport const shape: Shape = {value: 1};\n")
        self.write('src/unchanged.ts', "import {greet} from './a';\ngreet();\n")
        self.commit()
        self.cache = Path(self.directory.name) / 'cache'

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf-8')

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'fixture')
        self.head = self.git('rev-parse', 'HEAD')

    def job(self, **kwargs):
        return DependencyAnalysis(self.root, self.head, cache_dir=self.cache, **kwargs)


class CompilerTests(RepositoryFixture):
    def setUp(self):
        compiler = os.environ.get('CODE_FLOW_TEST_TYPESCRIPT')
        if not compiler or not (Path(compiler) / 'lib/typescript.js').is_file() or not shutil.which('node'):
            self.skipTest('Set CODE_FLOW_TEST_TYPESCRIPT to an installed TypeScript package directory; Node is required.')
        super().setUp()
        (self.root / 'node_modules').mkdir()
        (self.root / 'node_modules/typescript').symlink_to(Path(compiler).resolve(), target_is_directory=True)

    def edge(self, data, source, target, kind):
        return next(edge for edge in data['edges'] if (edge['source'], edge['target'], edge['kind']) == (source, target, kind))

    def test_aliases_reexports_type_references_and_unchanged_callers(self):
        data = self.job().analyse()
        self.assertTrue(data['coverage']['complete'], data['coverage'])
        self.edge(data, 'src/b.ts', 'src/barrel.ts', 'import')
        self.edge(data, 'src/barrel.ts', 'src/a.ts', 'import')
        self.edge(data, 'src/b.ts', 'src/a.ts', 'import')
        uses = self.edge(data, 'src/b.ts', 'src/a.ts', 'reference')['evidence']
        self.assertEqual([e['symbol'] for e in uses], ['greet', 'greet', 'Shape'])
        self.assertEqual([e['line'] for e in uses], [3, 3, 4])
        self.assertEqual(len({e['symbol_id'] for e in uses[:2]}), 1)
        self.edge(data, 'src/unchanged.ts', 'src/a.ts', 'reference')
        self.assertNotIn('manifest', data)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_verified_cache_and_dependency_config_invalidation(self):
        # A dependency-hosted tsconfig is part of the validation manifest.
        self.write('node_modules/config/base.json', '{"compilerOptions":{"strict":true}}')
        config = json.loads((self.root / 'tsconfig.json').read_text())
        config['extends'] = './node_modules/config/base.json'
        self.write('tsconfig.json', json.dumps(config))
        self.commit()
        self.assertFalse(self.job().analyse()['cacheHit'])
        self.assertTrue(self.job().analyse()['cacheHit'])
        self.write('node_modules/config/base.json', '{"compilerOptions":{"strict":false}}')
        self.assertFalse(self.job().analyse()['cacheHit'])
        cache_file = next(self.cache.glob('*.json'))
        cache_file.write_text('broken')
        self.assertFalse(self.job().analyse()['cacheHit'])

    def test_missing_module_and_files_outside_project_are_incomplete(self):
        self.write('src/missing.ts', "import {gone} from './does-not-exist';\ngone();\n")
        self.write('outside.ts', 'export const notCovered = 1;')
        self.commit()
        data = self.job().analyse()
        self.assertFalse(data['coverage']['complete'])
        self.assertEqual(data['coverage']['uncovered'], ['outside.ts'])
        self.assertTrue(any('unresolved import' in issue for issue in data['coverage']['issues']))
        self.assertFalse(any(e['target'].endswith('does-not-exist') for e in data['edges']))

    def test_project_references_find_source_without_a_build(self):
        self.write('tsconfig.json', '{"files":[],"references":[{"path":"lib"},{"path":"app"}]}')
        self.write('lib/tsconfig.json', '{"compilerOptions":{"composite":true},"include":["*.ts"]}')
        self.write('lib/index.ts', 'export function shared() { return 1; }')
        self.write('app/tsconfig.json', '{"compilerOptions":{"composite":true},"include":["*.ts"],"references":[{"path":"../lib"}]}')
        self.write('app/index.ts', "import {shared} from '../lib'; shared();")
        self.commit()
        data = self.job().analyse()
        self.edge(data, 'app/index.ts', 'lib/index.ts', 'import')
        self.edge(data, 'app/index.ts', 'lib/index.ts', 'reference')
        self.assertFalse((self.root / 'lib/index.js').exists())

    def test_duplicate_project_coverage_does_not_duplicate_evidence(self):
        self.write('tsconfig.extra.json', (self.root / 'tsconfig.json').read_text())
        self.commit()
        uses = self.edge(self.job().analyse(), 'src/b.ts', 'src/a.ts', 'reference')['evidence']
        self.assertEqual(len(uses), 3)

    def test_conditional_package_exports_respect_import_and_require_modes(self):
        config = json.loads((self.root / 'tsconfig.json').read_text())
        config['compilerOptions'].update(module='NodeNext', moduleResolution='NodeNext')
        config['include'] = ['src/**/*', 'lib/**/*']
        self.write('tsconfig.json', json.dumps(config))
        self.write('lib/package.json', json.dumps({'name': 'fixture', 'exports': {
            '.': {'import': './modern.mts', 'require': './legacy.cts'}}}))
        self.write('lib/modern.mts', 'export function value() { return 1; }')
        self.write('lib/legacy.cts', 'export function value() { return 2; }')
        self.write('src/consumer.mts', "import {value} from 'fixture'; value();")
        self.write('src/consumer.cts', "import {value} from 'fixture'; value();")
        (self.root / 'node_modules/fixture').symlink_to(self.root / 'lib', target_is_directory=True)
        self.commit()
        data = self.job().analyse()
        for kind in ('import', 'reference'):
            self.edge(data, 'src/consumer.mts', 'lib/modern.mts', kind)
            self.edge(data, 'src/consumer.cts', 'lib/legacy.cts', kind)

    def test_missing_dependency_becoming_available_invalidates_cached_resolution(self):
        self.write('src/external.ts', "import {value} from 'missing-package'; value();")
        self.commit()
        first = self.job().analyse()
        self.assertFalse(first['coverage']['complete'])
        self.assertTrue(self.job().analyse()['cacheHit'])
        self.write('node_modules/missing-package/package.json', '{"types":"index.d.ts"}')
        self.write('node_modules/missing-package/index.d.ts', 'export declare function value(): number;')
        resolved = self.job().analyse()
        self.assertFalse(resolved['cacheHit'])
        self.assertTrue(resolved['coverage']['complete'], resolved['coverage'])
        self.write('node_modules/missing-package/index.d.ts', 'export declare function value(): string;')
        self.assertFalse(self.job().analyse()['cacheHit'])

    def test_local_require_is_not_mistaken_for_an_import(self):
        self.write('src/local.ts', "const require = (name: string) => name; require('./not-a-module');\nconst empty = undefined;\nexport {};\n")
        self.commit()
        data = self.job().analyse()
        self.assertTrue(data['coverage']['complete'], data['coverage'])
        self.assertFalse(any(edge['source'] == 'src/local.ts' for edge in data['edges']))

    def test_dynamic_import_and_shorthand_reference(self):
        self.write('src/dynamic.ts', "import {greet} from './a';\nconst thing = {greet};\nconst later = import('./a');\nconst name = './a'; import(name);\n")
        self.commit()
        data = self.job().analyse()
        self.assertEqual(len(self.edge(data, 'src/dynamic.ts', 'src/a.ts', 'import')['evidence']), 2)
        self.assertEqual([e['line'] for e in self.edge(data, 'src/dynamic.ts', 'src/a.ts', 'reference')['evidence']], [2])
        self.assertTrue(any('non-literal' in issue for issue in data['coverage']['issues']))


class ProcessTests(RepositoryFixture):
    def test_analysis_uses_eight_gib_heap_limit(self):
        job = self.job()
        cached = json.dumps(dict(root=str(self.root), head=self.head, cacheHit=True)).encode()
        with patch('DependencyAnalysis.shutil.which', return_value='node'), patch.object(job, 'run', return_value=cached) as run:
            job.analyse()
        self.assertIn('--max-old-space-size=8192', run.call_args.args[0])

    def test_memory_failure_is_short_even_with_a_long_native_stack(self):
        script = ('import sys; '
                  'sys.stderr.write("FATAL ERROR: Reached heap limit - JavaScript heap out of memory\\n" '
                  '+ "123: 0x123456 Builtins_InterpreterEntryTrampoline\\n" * 300); '
                  'sys.exit(1)')
        with self.assertRaisesRegex(RuntimeError, 'out of memory.*8 GiB') as error:
            self.job().run([sys.executable, '-c', script], lambda _: None)
        self.assertNotIn('\n', str(error.exception))
        self.assertNotIn('Trampoline', str(error.exception))
        self.assertLess(len(str(error.exception)), 120)

    def test_missing_compiler_does_not_install(self):
        if not shutil.which('node'):
            self.skipTest('Node is required')
        with self.assertRaisesRegex(RuntimeError, 'TypeScript is not installed'):
            self.job().analyse()
        self.assertFalse((self.root / 'node_modules').exists())

    def test_dirty_or_wrong_head_blocks_analysis(self):
        self.write('src/a.ts', 'local edit')
        with self.assertRaisesRegex(RuntimeError, 'local changes'):
            self.job().analyse()
        self.commit()
        job = DependencyAnalysis(self.root, '0' * 40, cache_dir=self.cache)
        with self.assertRaisesRegex(RuntimeError, 'checkout changed'):
            job.analyse()

    def test_post_run_guard_discards_result_after_head_change(self):
        job = self.job()
        def worker(*args, **kwargs):
            self.write('src/a.ts', 'export const changed = 1;')
            self.commit()
            return b'{}'
        with patch.object(job, 'run', side_effect=worker):
            with self.assertRaisesRegex(RuntimeError, 'checkout changed'):
                job.analyse()
        self.assertEqual(list(self.cache.glob('*.json')), [])

    def test_cancel_and_timeout_stop_a_process(self):
        for cancel in (True, False):
            job = self.job(timeout=.2 if not cancel else 5)
            timer = threading.Timer(.15, job.cancel) if cancel else None
            if timer:
                timer.start()
            start = time.monotonic()
            try:
                with self.assertRaisesRegex(RuntimeError, 'cancelled|time limit'):
                    job.run([sys.executable, '-c', 'import time; time.sleep(30)'], lambda _: None)
            finally:
                if timer:
                    timer.cancel()
            self.assertLess(time.monotonic() - start, 3)

    def test_install_is_explicit_frozen_and_only_in_review_clone(self):
        self.write('pnpm-lock.yaml', 'lockfileVersion: 9')
        self.commit()
        job = self.job()
        with patch('DependencyAnalysis.shutil.which', return_value='pnpm'), patch.object(job, 'run') as run:
            job.install()
        self.assertEqual(run.call_args.args[0], ['pnpm', 'install', '--frozen-lockfile'])
        self.assertTrue(run.call_args.kwargs['install'])
        self.assertEqual(job.root, self.root)


if __name__ == '__main__':
    unittest.main()
