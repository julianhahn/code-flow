"""Build one review: checkout the reviewed snapshot and read its patches."""
import json
from pathlib import Path

from review_git import ReviewGit


class ReviewBuild:
    STEPS = ('Checkout', 'Read changes', 'Draw map')

    def __init__(self, root, branch, base, metadata=None):
        self.root = Path(root).resolve()
        self.branch, self.base, self.metadata = branch, base, metadata
        self.cancelled = False
        self.stage = 0

    def cancel(self):
        self.cancelled = True

    def check_cancelled(self):
        if self.cancelled:
            raise RuntimeError('Review build cancelled.')

    def run(self, progress):
        def report(stage, detail, fraction=0):
            self.check_cancelled()
            self.stage = stage
            progress(stage, detail, fraction)

        report(0, 'Checking the separate review clone…')
        repository = ReviewGit(self.root)
        repository.ensure_clean()
        selected, base = self.branch, self.base
        if self.metadata:
            number = str(self.metadata['number'])
            if not number.isdigit():
                raise ValueError('Enter a numeric PR number.')
            report(0, f'Checking PR #{number}…')
            raw = self.dependency.run(
                ['gh', 'pr', 'view', number, '--repo', 'plancraft/plancraft', '--json', 'baseRefName,headRefOid'],
                lambda message: report(0, message))
            current = json.loads(raw)
            report(0, 'Fetching the reviewed commit…')
            repository._git('fetch', 'origin', 'refs/pull/' + number + '/head')
            selected = repository.resolve_ref('FETCH_HEAD')
            if selected != current['headRefOid'] or selected != self.metadata['headRefOid']:
                raise ValueError('PR changed. Select it again to reload its overview.')
            report(0, 'Fetching the comparison base…')
            repository._git('fetch', 'origin', current['baseRefName'])
            base = repository.resolve_ref('FETCH_HEAD')
        report(0, 'Comparing commits…')
        comparison = repository.compare(base, selected)
        report(0, 'Checking out ' + comparison.head[:10] + '…')
        repository.checkout(comparison.head)

        files, file_commits = [], {}
        total = len(comparison.files)
        report(1, f'Reading {total} changed files…')
        for index, file in enumerate(comparison.files):
            report(1, file.path, index / max(1, total))
            files.append((file.status, file.old_path or file.path, file.path,
                          repository.file_patch(comparison, file)))
            self.check_cancelled()
            file_commits[file.path] = repository.file_commit(comparison, file)
            report(1, f'{index + 1} / {total} files', (index + 1) / max(1, total))

        self.check_cancelled()
        report(2, 'Preparing file cards…')
        return dict(merge=comparison.merge_base, head=comparison.head, files=files,
                    file_commits=file_commits)
