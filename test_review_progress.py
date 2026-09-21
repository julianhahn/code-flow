import tempfile
import unittest
from pathlib import Path
from review_progress import ReviewProgress


class ProgressTests(unittest.TestCase):
    def test_old_markers_without_commit_are_unread(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'old.db'
            file = ('M', 'a', 'a', 'patch')
            connection = sqlite3.connect(db)
            connection.execute('CREATE TABLE viewed (scope TEXT, path TEXT, fingerprint TEXT, PRIMARY KEY(scope,path))')
            connection.execute('INSERT INTO viewed VALUES (?, ?, ?)', ('pr', 'a', ReviewProgress.fingerprint(file)))
            connection.commit(); connection.close()
            progress = ReviewProgress('pr', db, 'head', {'a': 'commit'})
            self.assertFalse(progress.is_viewed(file))
            progress.set_viewed(file, True)
            self.assertTrue(progress.is_viewed(file))
            progress.close()

    def test_persistence_scope_and_changed_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'progress.db'
            file = ('M', 'a.ts', 'a.ts', '-old\n+new')
            store = ReviewProgress('pr:1', db)
            self.assertFalse(store.is_viewed(file))
            store.set_viewed(file, True)
            store.close()
            store = ReviewProgress('pr:1', db)
            self.assertTrue(store.is_viewed(file))
            self.assertFalse(store.is_viewed((*file[:3], '-old\n+changed')))
            other = ReviewProgress('pr:2', db)
            self.assertFalse(other.is_viewed(file))
            other.close()
            store.set_viewed(file, False)
            self.assertFalse(store.is_viewed(file))
            store.close()
