"""Build one review: checkout, always install, read patches, then analyse links."""
import json
from pathlib import Path

from DependencyAnalysis import DependencyAnalysis
from review_git import ReviewGit


class ReviewBuild:
    STEPS = ('Checkout', 'Install dependencies', 'Read changes', 'Find links', 'Draw map')

    def __init__(self, root, branch, base, metadata=None):
        self.root = Path(root).resolve()
        self.branch, self.base, self.metadata = branch, base, metadata
        self.dependency = DependencyAnalysis(self.root, '')
        self.stage = 0

    def cancel(self):
        self.dependency.cancel()

    def check_cancelled(self):
        self.dependency.check_cancelled()

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
        self.dependency.head = comparison.head

        # Julian's policy: every new map build installs dependencies, even when
        # node_modules and an analysis cache already exist. No install UI action.
        report(1, 'Running pnpm install --frozen-lockfile…')
        self.dependency.install(lambda message: report(1, message))

        files, file_commits = [], {}
        total = len(comparison.files)
        report(2, f'Reading {total} changed files…')
        for index, file in enumerate(comparison.files):
            report(2, file.path, index / max(1, total))
            files.append((file.status, file.old_path or file.path, file.path,
                          repository.file_patch(comparison, file)))
            self.check_cancelled()
            file_commits[file.path] = repository.file_commit(comparison, file)
            report(2, f'{index + 1} / {total} files', (index + 1) / max(1, total))

        report(3, 'Finding imports and symbol references…')
        links, links_error = None, None
        try:
            links = self.dependency.analyse(lambda message: report(3, message))
        except Exception as error:
            self.check_cancelled()  # Cancellation must not turn into a successful build.
            links_error = str(error)
        # A link-analysis failure may leave the diff usable. A changed or dirty
        # checkout must never be presented as the reviewed snapshot.
        self.dependency.repository()
        report(4, 'Preparing file cards…')
        return dict(merge=comparison.merge_base, head=comparison.head, files=files,
                    file_commits=file_commits, links=links, links_error=links_error)
