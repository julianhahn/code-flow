"""Local viewed markers keyed by review and exact file diff."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path


class ReviewProgress:
    def __init__(self, scope=None, database=None, head=None, file_commits=None):
        self.scope = scope or ''
        self.head = head
        self.file_commits = file_commits
        if database is None and scope:
            # Some launchers export XDG_STATE_HOME as an empty string. Treat
            # that like an unset variable, or SQLite ends up in the current
            # working directory and is easy to lose between launches.
            root = Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state')) / 'code-flow'
            root.mkdir(parents=True, exist_ok=True)
            database = root / 'review-progress.sqlite3'
        self.connection = sqlite3.connect(str(database) if database else ':memory:')
        self.connection.execute('CREATE TABLE IF NOT EXISTS viewed (scope TEXT, path TEXT, fingerprint TEXT, PRIMARY KEY(scope, path))')
        columns = {row[1] for row in self.connection.execute('PRAGMA table_info(viewed)')}
        for name in ('reviewed_head', 'file_commit'):
            if name not in columns:
                self.connection.execute(f'ALTER TABLE viewed ADD COLUMN {name} TEXT')
        self.connection.commit()

    @staticmethod
    def fingerprint(file):
        return hashlib.sha256(json.dumps(file, ensure_ascii=True).encode()).hexdigest()

    def is_viewed(self, file):
        row = self.connection.execute('SELECT fingerprint, file_commit FROM viewed WHERE scope=? AND path=?',
                                      (self.scope, file[2])).fetchone()
        if row is None or row[0] != self.fingerprint(file):
            return False
        if self.file_commits is None:
            return True
        current = self.file_commits.get(file[2])
        return bool(current) and row[1] == current

    def set_viewed(self, file, viewed):
        with self.connection:
            if viewed:
                self.connection.execute('INSERT OR REPLACE INTO viewed (scope, path, fingerprint, reviewed_head, file_commit) VALUES (?, ?, ?, ?, ?)',
                                        (self.scope, file[2], self.fingerprint(file), self.head,
                                         self.file_commits.get(file[2]) if self.file_commits is not None else None))
            else:
                self.connection.execute('DELETE FROM viewed WHERE scope=? AND path=?', (self.scope, file[2]))

    def close(self):
        self.connection.close()
