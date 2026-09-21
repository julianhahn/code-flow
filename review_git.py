"""Read Git snapshots and safely switch a dedicated review checkout."""
from dataclasses import dataclass
from pathlib import Path
import os
import subprocess


class ReviewGitError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChangedFile:
    status: str
    path: str
    old_path: str | None = None


@dataclass(frozen=True)
class Comparison:
    base: str
    head: str
    merge_base: str
    files: tuple[ChangedFile, ...]


class ReviewGit:
    def __init__(self, root, *, protected_root='/home/julian/plancraft'):
        self.root = Path(root).resolve()
        protected = Path(protected_root).resolve()
        if self.root == protected or self.root.is_relative_to(protected):
            raise ReviewGitError('The main checkout cannot be used for reviews')
        if not self.root.is_dir():
            raise ReviewGitError('Review checkout does not exist')
        actual = Path(self._git('rev-parse', '--show-toplevel').decode().strip()).resolve()
        if actual != self.root:
            raise ReviewGitError('Review path must be the repository root')
        # A linked worktree shares refs with the main checkout. Require a clone.
        git_dir = Path(self._git('rev-parse', '--absolute-git-dir').decode().strip()).resolve()
        if git_dir != self.root / '.git':
            raise ReviewGitError('Review checkout must be a separate clone, not a worktree')

    def _git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_TERMINAL_PROMPT='0', GIT_LITERAL_PATHSPECS='1',
                   GIT_SSH_COMMAND='ssh -oBatchMode=yes')
        try:
            result = subprocess.run(['git', *args], cwd=self.root, env=env,
                                    capture_output=True, timeout=45)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReviewGitError(str(error)) from error
        if result.returncode:
            raise ReviewGitError(result.stderr.decode(errors='replace').strip())
        return result.stdout

    def resolve_ref(self, ref):
        if not isinstance(ref, str) or not ref or ref.startswith('-') or '\0' in ref:
            raise ReviewGitError('Invalid Git reference')
        return self._git('rev-parse', '--verify', '--end-of-options',
                         ref + '^{commit}').decode().strip()

    def branches(self):
        return tuple(self._git('for-each-ref', '--format=%(refname)',
                               'refs/heads/', 'refs/remotes/').decode().splitlines())

    def default_branch(self):
        return self._git('symbolic-ref', 'refs/remotes/origin/HEAD').decode().strip()

    def ensure_clean(self):
        for name in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD',
                     'rebase-merge', 'rebase-apply', 'sequencer', 'BISECT_LOG'):
            location = Path(os.fsdecode(self._git('rev-parse', '--git-path', name)).strip())
            if not location.is_absolute():
                location = self.root / location
            if location.exists():
                raise ReviewGitError('An operation is in progress: ' + name)
        if self._git('status', '--porcelain=v1', '-z', '--untracked-files=all'):
            raise ReviewGitError('Review checkout has local changes')

    def checkout(self, ref):
        self.ensure_clean()
        sha = self.resolve_ref(ref)
        self._git('checkout', '--detach', '--no-overwrite-ignore', sha, '--')
        return sha

    def fetch(self):
        self.ensure_clean()
        self._git('fetch', '--no-recurse-submodules', 'origin')

    def compare(self, base, head='HEAD'):
        base_sha, head_sha = self.resolve_ref(base), self.resolve_ref(head)
        ancestor = self._git('merge-base', base_sha, head_sha).decode().strip()
        raw = self._git('diff', '--no-ext-diff', '--no-textconv', '--name-status',
                        '-z', '--find-renames', ancestor, head_sha, '--')
        fields = raw.split(b'\0')
        files = []
        index = 0
        while index < len(fields) - 1:
            status = fields[index].decode('ascii')
            path = os.fsdecode(fields[index + 1])
            index += 2
            old_path = None
            if status.startswith(('R', 'C')):
                old_path, path = path, os.fsdecode(fields[index])
                index += 1
            files.append(ChangedFile(status, path, old_path))
        return Comparison(base_sha, head_sha, ancestor, tuple(files))

    def file_commit(self, comparison, changed_file):
        if changed_file not in comparison.files:
            raise ReviewGitError('File does not belong to this comparison')
        paths = [changed_file.path]
        if changed_file.old_path:
            paths.append(changed_file.old_path)
        return self._git('log', '-1', '--format=%H', comparison.head, '--', *paths).decode().strip()

    def file_patch(self, comparison, changed_file):
        if changed_file not in comparison.files:
            raise ReviewGitError('File does not belong to this comparison')
        paths = [changed_file.path]
        if changed_file.old_path:
            paths.insert(0, changed_file.old_path)
        return self._git('diff', '--no-ext-diff', '--no-textconv', '--no-color',
                         '--find-renames', '--unified=3',
                         self.resolve_ref(comparison.merge_base),
                         self.resolve_ref(comparison.head), '--', *paths).decode(errors='replace')
