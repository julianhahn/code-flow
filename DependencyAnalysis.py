"""Bounded, cancellable analysis in a dedicated review clone, with verified cache."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from review_git import ReviewGit


class DependencyAnalysis:
    def __init__(self, root, head, *, cache_dir=None, timeout=110):
        self.root = Path(root).resolve()
        self.head = head
        self.timeout = min(110, timeout)
        self.cancelled = threading.Event()
        self.process = None
        self.process_lock = threading.Lock()
        self.worker = Path(__file__).with_name('dependency_worker.cjs')
        self.cache_dir = Path(cache_dir) if cache_dir else Path(
            os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'code-flow' / 'dependencies'

    def cancel(self):
        self.cancelled.set()
        # Closing the window must also stop pnpm, even if Python exits before
        # the background thread gets another polling turn.
        with self.process_lock:
            process = self.process
        if process is not None:
            self.stop(process)

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise RuntimeError('Dependency task cancelled.')

    def repository(self):
        self.check_cancelled()
        repository = ReviewGit(self.root)
        repository.ensure_clean()
        if repository.resolve_ref('HEAD') != self.head:
            raise RuntimeError('Review checkout changed. Reopen the diff map before analysing links.')
        self.check_cancelled()
        return repository

    def stop(self, process):
        # Cancel and the worker's finally block can arrive together. Stop each
        # process group only once; never signal its old PID after it was reaped.
        with self.process_lock:
            if self.process is not process:
                return
            def send(sig):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # macOS can return EPERM for an already departed group.
                    if process.poll() is None:
                        raise
            try:
                send(signal.SIGTERM)
                try:
                    process.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    pass
                send(signal.SIGKILL)  # Also stop remaining lifecycle children.
                process.wait(timeout=2)
            finally:
                self.process = None

    def run(self, command, progress, *, install=False):
        self.check_cancelled()
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        env.update(GIT_TERMINAL_PROMPT='0', GIT_SSH_COMMAND='ssh -oBatchMode=yes', CI='1',
                   COREPACK_ENABLE_DOWNLOAD_PROMPT='0', NO_COLOR='1')
        with tempfile.TemporaryFile(mode='w+b') as output, tempfile.TemporaryFile(mode='w+b') as log:
            with self.process_lock:
                self.check_cancelled()
                process = subprocess.Popen(command, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                           stdout=log if install else output, stderr=log,
                                           start_new_session=True)
                self.process = process
            with process:
                start = time.monotonic()
                offset = 0
                try:
                    while True:
                        self.check_cancelled()
                        if time.monotonic() - start > self.timeout:
                            raise RuntimeError('Dependency task reached its time limit. Reopen the diff map to retry, or prepare dependencies in a terminal.')
                        # pread does not move the child's shared file offset.
                        chunk = os.pread(log.fileno(), 32768, offset)
                        if chunk:
                            offset += len(chunk)
                            lines = chunk.decode(errors='replace').strip().splitlines()
                            if lines:
                                progress(lines[-1][:400])
                        if process.poll() is not None:
                            break
                        self.cancelled.wait(.08)
                    self.check_cancelled()
                    if process.returncode:
                        size = os.fstat(log.fileno()).st_size
                        diagnostic = os.pread(log.fileno(), 65536, max(0, size - 65536)).decode(errors='replace')
                        if not install and 'out of memory' in diagnostic.lower():
                            message = 'Link analysis ran out of memory (8 GiB JavaScript heap limit).'
                        elif not install and process.returncode < 0:
                            message = f'Link analysis crashed (signal {-process.returncode}).'
                        else:
                            lines = [line.strip() for line in diagnostic.splitlines() if line.strip()]
                            message = next((line for line in reversed(lines)
                                            if 'ERROR:' in line or 'ERR_PNPM_' in line or 'FATAL ERROR:' in line),
                                           lines[-1] if lines else f'Dependency task exited with code {process.returncode}.')
                        raise RuntimeError(' '.join(message.split())[:500])
                    output.seek(0)
                    if os.fstat(output.fileno()).st_size > 100 * 1024 * 1024:
                        raise RuntimeError('Dependency result exceeds 100 MiB. Narrow the project configuration.')
                    return output.read()
                finally:
                    # Also stops lifecycle children if their parent exited first.
                    self.stop(process)

    def analyse(self, progress=lambda message: None):
        progress('Checking review checkout…')
        repository = self.repository()
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node is not on the app command search path. Install Node and restart the app.')
        files = [os.fsdecode(file) for file in repository._git('ls-files', '-z').split(b'\0') if file]
        version = hashlib.sha256(self.worker.read_bytes()).hexdigest()
        key = hashlib.sha256(json.dumps([str(self.root), self.head, version]).encode()).hexdigest()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache = self.cache_dir / (key + '.json')
        request = dict(root=str(self.root), head=self.head, files=files, cache=str(cache))
        with tempfile.TemporaryDirectory(prefix='code-flow-links-') as directory:
            request_path = Path(directory) / 'request.json'
            request_path.write_text(json.dumps(request), encoding='utf-8')
            raw = self.run([node, '--max-old-space-size=8192', str(self.worker), str(request_path)], progress)
        self.repository()  # Never publish results after a checkout change or local edit.
        data = json.loads(raw)
        if data.get('head') != self.head or data.get('root') != str(self.root):
            raise RuntimeError('Dependency result does not match the reviewed commit.')
        if not data.get('cacheHit'):
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.cache_dir, prefix=key, suffix='.tmp', delete=False) as file:
                    temporary = Path(file.name)
                    file.write(raw)
                self.check_cancelled()
                temporary.replace(cache)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        # The potentially large validation manifest belongs in the cache, not GTK.
        data.pop('manifest', None)
        return data

    def install(self, progress=lambda message: None):
        self.repository()
        pnpm = shutil.which('pnpm')
        if not pnpm:
            raise RuntimeError('pnpm is not on the app command search path. Install pnpm and restart the app.')
        if not (self.root / 'pnpm-lock.yaml').is_file():
            raise RuntimeError('No pnpm-lock.yaml in the review clone. Prepare this repository’s dependencies manually.')
        progress('Installing dependencies in the review clone…')
        self.run([pnpm, 'install', '--frozen-lockfile'], progress, install=True)
        self.repository()  # Lifecycle scripts may have edited tracked files.
        progress('Dependencies installed. Ready to analyse.')
