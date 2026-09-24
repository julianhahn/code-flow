import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from reference_links import resolve_changed_imports


class ChangedImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.typescript_root = Path.home() / 'plancraft-review'
        try:
            subprocess.run(['node', '-e', "require.resolve('typescript')"], cwd=cls.typescript_root,
                           capture_output=True, timeout=5, check=True)
        except (OSError, subprocess.SubprocessError):
            raise unittest.SkipTest('TypeScript is not installed in the review clone')

    def test_resolves_direct_imports_and_types_only_between_changed_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tsconfig.json').write_text('{"compilerOptions":{"moduleResolution":"Bundler","module":"ESNext"},"include":["src/**/*.ts"]}')
            (root / 'src').mkdir()
            (root / 'node_modules').symlink_to(self.typescript_root / 'node_modules', target_is_directory=True)
            (root / 'src/use.ts').write_text("import { run, type Input } from './core';\nexport { run as alsoRun } from './core';\nimport { DateTime } from 'luxon';\n")
            (root / 'src/core.ts').write_text('export type Input = string; export const run = () => 1;\n')
            links = resolve_changed_imports(root, ['src/use.ts', 'src/core.ts'])
        self.assertEqual(links['src/use.ts'], [
            {'to': 'src/core.ts', 'kind': 'imports from', 'symbol': 'run', 'line': 1},
            {'to': 'src/core.ts', 'kind': 'imports type from', 'symbol': 'Input', 'line': 1},
            {'to': 'src/core.ts', 'kind': 're-exports from', 'symbol': 'run', 'line': 2},
        ])
        self.assertEqual(links['src/core.ts'], [])

    def test_resolves_calls_and_fixture_reads_between_changed_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tsconfig.json').write_text('{"compilerOptions":{"moduleResolution":"Bundler","module":"ESNext"},"include":["src/**/*.ts"]}')
            (root / 'src/fixtures').mkdir(parents=True)
            (root / 'node_modules').symlink_to(self.typescript_root / 'node_modules', target_is_directory=True)
            (root / 'src/fixtures/data.xml').write_text('<receipt/>')
            (root / 'src/use.spec.ts').write_text("import { readFileSync } from 'node:fs';\nimport { run } from './core';\nrun();\nreadFileSync(new URL('./fixtures/data.xml', import.meta.url), 'utf8');\n")
            (root / 'src/core.ts').write_text('export const run = () => 1;\n')
            links = resolve_changed_imports(root, ['src/use.spec.ts', 'src/core.ts', 'src/fixtures/data.xml'])
        self.assertIn({'to': 'src/core.ts', 'kind': 'calls', 'symbol': 'run', 'line': 3}, links['src/use.spec.ts'])
        self.assertIn({'to': 'src/fixtures/data.xml', 'kind': 'reads', 'line': 4}, links['src/use.spec.ts'])


if __name__ == '__main__':
    unittest.main()
