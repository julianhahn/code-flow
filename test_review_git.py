import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from review_git import ReviewGit, ReviewGitError


class ReviewGitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        (self.root / 'a.txt').write_text('base\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.reader = ReviewGit(self.root)

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess.run(['git', *args], cwd=self.root, env=env, check=True,
                              capture_output=True, text=True, timeout=10).stdout

    def test_default_protection_follows_home(self):
        with patch('review_git.Path.home', return_value=self.root):
            with self.assertRaisesRegex(ReviewGitError, 'main checkout'):
                ReviewGit(self.root / 'plancraft')
            with self.assertRaisesRegex(ReviewGitError, 'main checkout'):
                ReviewGit(self.root / 'plancraft' / 'nested')
            ReviewGit(self.root)

    def test_merge_base_pinned_and_ignores_working_tree(self):
        self.git('checkout', '-b', 'feature/slash')
        (self.root / 'a.txt').write_text('feature\n')
        self.git('commit', '-am', 'feature')
        head = self.git('rev-parse', 'HEAD').strip()
        self.git('checkout', 'main')
        (self.root / 'base-only.txt').write_text('base branch change')
        self.git('add', '.')
        self.git('commit', '-m', 'advance base')
        comparison = self.reader.compare('main', 'feature/slash')
        self.assertEqual(comparison.merge_base, self.base)
        self.assertEqual(comparison.head, head)
        self.assertEqual([f.path for f in comparison.files], ['a.txt'])
        (self.root / 'a.txt').write_text('uncommitted\n')
        patch = self.reader.file_patch(comparison, comparison.files[0])
        self.assertIn('+feature', patch)
        self.assertNotIn('uncommitted', patch)

    def test_viewed_commit_invalidated_even_when_patch_returns_to_same_content(self):
        from review_progress import ReviewProgress
        (self.root / 'a.txt').write_text('reviewed\n')
        self.git('commit', '-am', 'first')
        initial = self.reader.compare(self.base)
        f = initial.files[0]
        file = (f.status, f.path, f.path, self.reader.file_patch(initial, f))
        commit = self.reader.file_commit(initial, f)
        db = self.root / '.git' / 'viewed.db'
        progress = ReviewProgress('pr', db, initial.head, {f.path: commit})
        progress.set_viewed(file, True)
        saved = progress.connection.execute('SELECT reviewed_head, file_commit FROM viewed').fetchone()
        self.assertEqual(saved, (initial.head, commit))
        progress.close()
        (self.root / 'other.txt').write_text('unrelated')
        self.git('add', 'other.txt'); self.git('commit', '-m', 'unrelated')
        unrelated = self.reader.compare(self.base)
        self.assertEqual(self.reader.file_commit(unrelated, unrelated.files[0]), commit)
        progress = ReviewProgress('pr', db, unrelated.head, {'a.txt': commit})
        self.assertTrue(progress.is_viewed(file)); progress.close()
        (self.root / 'a.txt').write_text('changed\n')
        self.git('commit', '-am', 'change')
        (self.root / 'a.txt').write_text('reviewed\n')
        self.git('commit', '-am', 'restore')
        latest = self.reader.compare(self.base)
        current = latest.files[0]
        self.assertEqual(self.reader.file_patch(latest, current), file[3])
        new_commit = self.reader.file_commit(latest, current)
        self.assertNotEqual(new_commit, commit)
        progress = ReviewProgress('pr', db, latest.head, {'a.txt': new_commit})
        self.assertFalse(progress.is_viewed(file)); progress.close()

    def test_rename_and_unusual_paths(self):
        target = 'renamed\tline\n[1].txt'
        self.git('mv', 'a.txt', target)
        self.git('commit', '-m', 'rename')
        comparison = self.reader.compare(self.base)
        self.assertEqual(comparison.files[0].old_path, 'a.txt')
        self.assertEqual(comparison.files[0].path, target)
        self.assertIn('rename from', self.reader.file_patch(comparison, comparison.files[0]))

    def test_reject_dirty_and_in_progress(self):
        (self.root / 'untracked').write_text('keep')
        with self.assertRaises(ReviewGitError):
            self.reader.checkout(self.base)
        (self.root / 'untracked').unlink()
        (self.root / '.git' / 'MERGE_HEAD').write_text(self.base)
        with self.assertRaises(ReviewGitError):
            self.reader.checkout(self.base)

    def test_safe_detached_checkout(self):
        self.assertEqual(self.reader.checkout('main'), self.base)
        self.assertEqual(self.git('rev-parse', '--abbrev-ref', 'HEAD').strip(), 'HEAD')

    def test_ref_options_and_missing_ref_rejected(self):
        for ref in ('--help', '', 'missing-branch', 'bad\0ref'):
            with self.subTest(ref=ref), self.assertRaises(ReviewGitError):
                self.reader.resolve_ref(ref)

    def test_protected_root_rejected(self):
        with self.assertRaises(ReviewGitError):
            ReviewGit(self.root, protected_root=self.root)

    def test_branch_list_and_default(self):
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main')
        self.assertIn('refs/heads/main', self.reader.branches())
        self.assertEqual(self.reader.default_branch(), 'refs/remotes/origin/main')

    def test_added_deleted_binary_and_literal_path(self):
        (self.root / 'a.txt').unlink()
        (self.root / '[x].txt').write_text('literal\n')
        (self.root / 'x.txt').write_text('other\n')
        (self.root / 'binary').write_bytes(b'\0binary')
        self.git('add', '-A')
        self.git('commit', '-m', 'files')
        comparison = self.reader.compare(self.base)
        by_path = {f.path: f for f in comparison.files}
        self.assertEqual(by_path['a.txt'].status, 'D')
        self.assertEqual(by_path['binary'].status, 'A')
        patch = self.reader.file_patch(comparison, by_path['[x].txt'])
        self.assertIn('+literal', patch)
        self.assertNotIn('+other', patch)
        self.assertIn('Binary files', self.reader.file_patch(comparison, by_path['binary']))


if __name__ == '__main__':
    unittest.main()
