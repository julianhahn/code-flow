#!/usr/bin/python3
"""Read-only diff canvas. Git operations run outside the GTK event loop."""
import os
import json
import re
import subprocess
import threading
from pathlib import Path
from review_summary import checks_summary, purpose_excerpt
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk

ROOT = Path.home() / 'plancraft-review'


def git(*args):
    result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True,
                            text=True, timeout=60, env={**{k: v for k, v in os.environ.items() if not k.startswith('GIT_')}, 'GIT_TERMINAL_PROMPT': '0', 'GIT_SSH_COMMAND': 'ssh -oBatchMode=yes'})
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return result.stdout.strip()


class ReviewWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title='Code Flow — Review')
        self.set_name('code-flow-review')
        self.set_wmclass('code-flow', 'code-flow')
        self.set_icon_from_file(str(Path(__file__).with_name('code-flow.svg')))
        style = Gtk.CssProvider()
        style.load_from_path(str(Path(__file__).with_name('review.css')))
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        selection_style = Gtk.CssProvider()
        selection_style.load_from_data(b'''
            #code-flow-review textview text selection,
            #code-flow-review textview text selection:focus,
            #code-flow-review textview text selection:backdrop,
            #code-flow-review entry selection {
                background-color: #2457c5;
                color: #ffffff;
            }
        ''')
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), selection_style,
                                                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+2)
        self.set_default_size(1400, 900)
        self.connect('destroy', Gtk.main_quit)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)
        self.add(box)
        bar = Gtk.Box(spacing=8)
        self.branch = Gtk.Entry(placeholder_text='Search branches…')
        self.branch_options = []
        self.branch.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, 'pan-down-symbolic')
        self.branch_menu = Gtk.Popover.new(self.branch)
        self.branch_menu.set_modal(False)
        self.branch_menu.set_position(Gtk.PositionType.BOTTOM)
        self.branch_list = Gtk.ListBox()
        self.branch_list.connect('row-activated', self.choose_branch)
        choices = Gtk.ScrolledWindow()
        choices.set_size_request(520, 300)
        choices.add(self.branch_list)
        self.branch_menu.add(choices)
        self.branch.connect('changed', self.filter_branches)
        self.branch.connect('icon-press', self.toggle_branches)
        self.branch.connect('key-press-event', self.branch_key)
        self.base = Gtk.Entry(placeholder_text='Base branch')
        self.pr = Gtk.Entry(placeholder_text='PR number — press Enter')
        self.pr.connect('activate', self.load_overview)
        self.branch.connect('activate', self.load_overview)
        self.mode = Gtk.ComboBoxText()
        self.mode.append_text('Branch')
        self.mode.append_text('Pull request')
        self.mode.set_active(0)
        self.selector = Gtk.Stack()
        self.selector.add_named(self.branch, 'branch')
        self.selector.add_named(self.pr, 'pr')
        self.mode.connect('changed', self.change_mode)
        bar.pack_start(self.mode, False, False, 0)
        bar.pack_start(self.selector, True, True, 0)
        self.overview_pr = None
        self.overview_branch = None
        for label, callback in [('↻', self.refresh), ('Flow mode', self.flow)]:
            button = Gtk.Button(label=label)
            button.connect('clicked', callback)
            bar.pack_start(button, False, False, 0)
        self.bar = bar
        box.pack_start(bar, False, False, 0)
        self.status = Gtk.Label(label='Loading branches…', xalign=0)
        self.status.set_selectable(True)
        self.status.get_style_context().add_class('muted')
        box.pack_start(self.status, False, False, 0)
        self.actions = Gtk.Box(spacing=12)
        self.actions.set_no_show_all(True)
        self.diff_button = Gtk.Button(label='Open diff map')
        self.diff_button.get_style_context().add_class('primary')
        self.diff_button.connect('clicked', self.toggle_review_view)
        self.github_button = Gtk.LinkButton.new_with_label('https://github.com/plancraft/plancraft', 'View on GitHub')
        self.github_button.set_no_show_all(True)
        self.actions.pack_start(self.diff_button, False, False, 0)
        self.actions.pack_start(self.github_button, False, False, 0)
        box.pack_start(self.actions, False, False, 0)
        self.canvas = Gtk.Box(spacing=24)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.canvas)
        self.views = Gtk.Stack()
        self.views.add_named(scroll, 'overview')
        from chat_panel import ChatPanel
        self.review_head = None
        self.chat = ChatPanel(ROOT, self.chat_context, self.chat_busy)
        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        split.pack1(self.views, resize=True, shrink=False)
        split.pack2(self.chat, resize=False, shrink=False)
        split.set_position(950)
        box.pack_start(split, True, True, 0)
        self.connect('destroy', lambda *_: self.chat.client.stop() if self.chat.client else None)
        self.refresh(fetch=False)

    def toggle_review_view(self, *_):
        if self.views.get_visible_child_name() == 'map':
            self.show_overview((self.overview_branch, self.overview_pr))
        else:
            self.open_review()

    def chat_busy(self, busy):
        self.bar.set_sensitive(not busy)
        self.actions.set_sensitive(not busy)

    def chat_context(self):
        if not self.overview_pr:
            raise ValueError('Select a PR to open its own chat.')
        if self.review_head != self.overview_pr['headRefOid']:
            raise ValueError('Open the diff map first so Pi can read the reviewed code.')
        board = self.views.get_child_by_name('map')
        active = board.active_file if board and self.views.get_visible_child_name() == 'map' else None
        selected = board.selected_context if board else None
        selection = selected['text'] if selected else ''
        if selected:
            active = selected['file']
        return dict(pr=self.overview_pr['number'], url=self.overview_pr['url'],
                    title=self.overview_pr['title'], head=self.review_head,
                    description=self.overview_pr.get('body', '')[:12000],
                    file=active[2] if active else None,
                    diff=active[3][:50000] if active else None,
                    diff_truncated=bool(active and len(active[3]) > 50000),
                    selection=selection[:12000], selection_truncated=len(selection) > 12000,
                    selection_diff_rows=[selected['first'], selected['last']] if selected else None)

    def change_mode(self, *_):
        self.selector.set_visible_child_name('branch' if self.mode.get_active() == 0 else 'pr')
        self.branch_menu.popdown()

    @staticmethod
    def github(*args):
        result = subprocess.run(['gh', 'pr', *args, '--repo', 'plancraft/plancraft'],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError(result.stderr.strip())
        return json.loads(result.stdout)

    def load_overview(self, *_):
        branch = self.branch.get_text().strip()
        number = self.pr.get_text().strip() if self.mode.get_active() == 1 else None
        self.branch_menu.popdown()
        self.clear_canvas()
        self.overview_pr = None
        self.overview_branch = None
        self.review_head = None
        self.actions.hide()
        self.status.set_text('Loading PR overview…')
        def task():
            fields = 'number,title,body,url,state,isDraft,baseRefName,headRefName,headRefOid,reviewDecision,statusCheckRollup,changedFiles,additions,deletions'
            if number is not None:
                if not number.isdigit():
                    raise ValueError('Enter a PR number, then press Enter.')
                metadata = self.github('view', number, '--json', fields)
            else:
                if not branch:
                    raise ValueError('Select a branch first.')
                matches = self.github('list', '--head', branch.removeprefix('origin/'),
                                      '--state', 'all', '--limit', '100', '--json', 'number,state,updatedAt')
                matches.sort(key=lambda p: (p['state'] == 'OPEN', p['updatedAt']), reverse=True)
                metadata = self.github('view', str(matches[0]['number']), '--json', fields) if matches else None
            return branch, metadata
        self.work(task, self.show_overview)

    def clear_canvas(self):
        self.views.set_visible_child_name('overview')
        for child in self.canvas.get_children():
            self.canvas.remove(child)

    def show_overview(self, result):
        self.diff_button.set_label('Open diff map')
        branch, metadata = result
        self.overview_branch = branch
        self.overview_pr = metadata
        self.chat.configure(metadata['number'] if metadata else None)
        self.clear_canvas()
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=16)
        panel.set_size_request(700, -1)
        panel.get_style_context().add_class('overview-card')
        panel.set_valign(Gtk.Align.START)
        self.canvas.pack_start(panel, True, True, 0)
        def label(text, *classes):
            widget = Gtk.Label(label=text, xalign=0, selectable=True)
            widget.set_line_wrap(True)
            widget.set_max_width_chars(85)
            for name in classes:
                widget.get_style_context().add_class(name)
            if 'badge' in classes:
                widget.set_halign(Gtk.Align.START)
            panel.pack_start(widget, False, False, 0)
        if metadata:
            label(f"PULL REQUEST  #{metadata['number']}", 'eyebrow')
            label(metadata['title'], 'review-title')
            state = 'Draft' if metadata['state'] == 'OPEN' and metadata['isDraft'] else metadata['state'].capitalize()
            decision = {'APPROVED': 'Approved', 'CHANGES_REQUESTED': 'Changes requested',
                        'REVIEW_REQUIRED': 'Review required'}.get(metadata.get('reviewDecision'), 'No review decision')
            label(f"{state} · {decision}", 'badge',
                  'attention' if metadata.get('reviewDecision') == 'CHANGES_REQUESTED' else 'neutral')
            label(f"{metadata['headRefName']}  →  {metadata['baseRefName']}", 'muted')
            checks = checks_summary(metadata.get('statusCheckRollup'))
            label(checks, 'badge', 'good' if checks == 'Checks: no failures' else 'neutral')
            if 'changedFiles' in metadata:
                stats = Gtk.Box(spacing=24)
                stats.get_style_context().add_class('stats')
                for text, color in (
                    (f"{metadata['changedFiles']} changed files", 'stat-files'),
                    (f"+{metadata['additions']:,} added", 'stat-added'),
                    (f"−{metadata['deletions']:,} removed", 'stat-removed'),
                ):
                    count = Gtk.Label(label=text, xalign=0, selectable=True)
                    count.get_style_context().add_class(color)
                    stats.pack_start(count, False, False, 0)
                panel.pack_start(stats, False, False, 0)
            label('PURPOSE', 'section-title')
            label(purpose_excerpt(metadata.get('body')), 'purpose')
            label('From the PR description · Full details and discussion on GitHub', 'muted')
            self.github_button.set_uri(metadata['url'])
            self.github_button.show()
        else:
            self.github_button.hide()
            label(branch)
            label('No pull request found for this branch.')
            label('Compare with')
            parent = self.base.get_parent()
            if parent:
                parent.remove(self.base)
            panel.pack_start(self.base, False, False, 0)
        self.diff_button.show()
        self.actions.show()
        self.status.set_text('Overview · Details and discussion on GitHub.')
        self.canvas.show_all()

    def filter_branches(self, *_):
        for row in self.branch_list.get_children():
            self.branch_list.remove(row)
        query = self.branch.get_text().casefold()
        matches = [name for name in self.branch_options if query in name.casefold()]
        for name in matches:
            label = Gtk.Label(label=name, xalign=0, margin=6)
            self.branch_list.add(label)
        if not matches:
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            row.add(Gtk.Label(label='No matching branches', margin=6))
            self.branch_list.add(row)
        self.branch_menu.show_all()
        self.branch_menu.popup()

    def choose_branch(self, _, row):
        if not row.get_activatable():
            return
        self.branch.set_text(row.get_child().get_text())
        self.branch_menu.popdown()
        self.branch.grab_focus()
        self.branch.set_position(-1)
        self.load_overview()

    def toggle_branches(self, *_):
        if self.branch_menu.get_visible():
            self.branch_menu.popdown()
        else:
            self.filter_branches()

    def branch_key(self, _, event):
        from gi.repository import Gdk
        if event.keyval == Gdk.KEY_Escape:
            self.branch_menu.popdown()
            return True
        if event.keyval == Gdk.KEY_Down:
            self.filter_branches()
            first = self.branch_list.get_row_at_index(0)
            if first and first.get_activatable():
                first.grab_focus()
            return True
        return False

    def work(self, task, done):
        self.bar.set_sensitive(False)
        self.actions.set_sensitive(False)
        def run():
            try:
                value = task()
            except Exception as error:
                GLib.idle_add(self.failed, str(error))
            else:
                GLib.idle_add(self.complete, done, value)
        threading.Thread(target=run, daemon=True).start()

    def failed(self, message):
        self.status.set_text(message)
        self.actions.set_sensitive(True)
        self.bar.set_sensitive(True)
        self.canvas.set_sensitive(True)
        return False

    def complete(self, callback, value):
        try:
            callback(value)
        except Exception as error:
            self.status.set_text(str(error))
        self.actions.set_sensitive(True)
        self.bar.set_sensitive(True)
        self.canvas.set_sensitive(True)
        return False

    def refresh(self, *_, fetch=True):
        def task():
            from review_git import ReviewGit
            repository = ReviewGit(ROOT)
            if fetch:
                repository.fetch()
            branches = git('for-each-ref', '--format=%(refname:short)', 'refs/heads', 'refs/remotes/origin').splitlines()
            default = git('symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
            return branches, default
        def done(value):
            branches, default = value
            self.branch_options.clear()
            for branch in branches:
                if branch != 'origin/HEAD':
                    self.branch_options.append(branch)
            self.base.set_text(default)
            self.status.set_text('Select a branch to load its PR overview.')
        self.work(task, done)

    def open_review(self, *_):
        branch = self.overview_branch
        base = self.base.get_text().strip()
        metadata_snapshot = self.overview_pr
        pr = str(metadata_snapshot['number']) if metadata_snapshot else ''
        self.canvas.set_sensitive(False)
        self.status.set_text('Loading diff map…')
        def task():
            from review_git import ReviewGit
            repository = ReviewGit(ROOT)
            repository.ensure_clean()
            selected, target_base = branch, base
            if pr:
                if not pr.isdigit():
                    raise ValueError('Enter a numeric PR number.')
                import json
                result = subprocess.run(['gh', 'pr', 'view', pr, '--repo', 'plancraft/plancraft', '--json', 'baseRefName,headRefOid'], capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise ValueError(result.stderr.strip())
                metadata = json.loads(result.stdout)
                git('fetch', 'origin', 'refs/pull/' + pr + '/head')
                selected = git('rev-parse', 'FETCH_HEAD')
                if selected != metadata['headRefOid'] or selected != metadata_snapshot['headRefOid']:
                    raise ValueError('PR changed. Select it again to reload its overview.')
                git('fetch', 'origin', metadata['baseRefName'])
                target_base = git('rev-parse', 'FETCH_HEAD')
            comparison = repository.compare(target_base, selected)
            repository.checkout(comparison.head)
            files = [(f.status, f.old_path or f.path, f.path, repository.file_patch(comparison, f))
                     for f in comparison.files]
            file_commits = {f.path: repository.file_commit(comparison, f) for f in comparison.files}
            return comparison.merge_base, comparison.head, files, file_commits
        self.work(task, self.show_review)

    def show_review(self, review):
        from diff_canvas import DiffCanvas
        merge, head, files, file_commits = review
        self.review_head = head
        previous = self.views.get_child_by_name('map')
        if previous:
            self.views.remove(previous)
            previous.destroy()
        scope = (f'plancraft/plancraft:pr:{self.overview_pr["number"]}' if self.overview_pr
                 else f'{ROOT}:branch:{self.overview_branch}:base:{self.base.get_text()}')
        board = DiffCanvas(files, review_scope=scope, head=head, file_commits=file_commits)
        self.views.add_named(board, 'map')
        board.show_all()
        self.views.set_visible_child_name('map')
        self.diff_button.set_label('Back to overview')
        self.status.set_text(f'{len(files)} files · {merge[:10]} → {head[:10]} · Diff map')

    def legacy_show_review(self, review):
        merge, head, files = review
        for child in self.canvas.get_children():
            self.canvas.remove(child)
        columns = {}
        for name in ('DBTypes', 'Frontend', 'Shared/API', 'Backend', 'Other'):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            column.set_size_request(520, -1)
            column.set_valign(Gtk.Align.START)
            column.pack_start(Gtk.Label(label=name, xalign=0), False, False, 0)
            self.canvas.pack_start(column, False, False, 0)
            columns[name] = column
        for status, old, new in files:
            parts = new.split('/')
            package = parts[1] if len(parts) > 2 and parts[0] == 'pkgs' else ''
            group = ('DBTypes' if package in ('dbtypes', 'db-types') else
                     'Frontend' if '-frontend' in package else
                     'Backend' if '-backend' in package else
                     'Shared/API' if package.endswith('-shared') else 'Other')
            expander = Gtk.Expander(label=f'{status}  {new}')
            expander.connect('notify::expanded', self.expand_patch, merge, head, old, new)
            columns[group].pack_start(expander, False, False, 0)
        self.status.set_text(f'{len(files)} files · merge base {merge[:10]} → head {head[:10]} · detached review checkout')
        self.canvas.show_all()

    def expand_patch(self, expander, _, merge, head, old, new):
        if not expander.get_expanded() or expander.get_child():
            return
        label = Gtk.Label(label='Loading patch…')
        expander.add(label)
        label.show()
        def run():
            try:
                patch = git('--literal-pathspecs', 'diff', '--no-ext-diff', '--no-textconv', '--no-color', '--unified=5', merge, head, '--', old, new)
            except Exception as error:
                GLib.idle_add(label.set_text, str(error))
                return
            GLib.idle_add(self.patch_ready, expander, label, patch)
        threading.Thread(target=run, daemon=True).start()

    def patch_ready(self, expander, label, patch):
        expander.remove(label)
        text = Gtk.TextView(editable=False, monospace=True)
        buffer = text.get_buffer()
        buffer.create_tag('add', background='#d8f5dc', foreground='#143d20')
        buffer.create_tag('remove', background='#ffe0e0', foreground='#562020')
        buffer.create_tag('hunk', background='#daeafa', foreground='#193b60')
        old_line = new_line = None
        for line in patch.splitlines():
            match = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if match:
                old_line, new_line = map(int, match.groups())
            tag = 'add' if line.startswith('+') else 'remove' if line.startswith('-') else 'hunk' if line.startswith('@@') else None
            shown = line
            if old_line is not None and line[:1] in (' ', '+', '-'):
                old_number = str(old_line) if not line.startswith('+') else ''
                new_number = str(new_line) if not line.startswith('-') else ''
                shown = f'{old_number:>5} {new_number:>5}  {line}'
                old_line += not line.startswith('+')
                new_line += not line.startswith('-')
            if tag:
                buffer.insert_with_tags_by_name(buffer.get_end_iter(), shown + '\n', tag)
            else:
                buffer.insert(buffer.get_end_iter(), shown + '\n')
        scroller = Gtk.ScrolledWindow()
        scroller.set_size_request(650, 500)
        scroller.add(text)
        expander.add(scroller)
        expander.show_all()
        return False

    def flow(self, *_):
        subprocess.Popen(['/usr/bin/python3', str(Path(__file__).with_name('code-flow.py'))], env={**os.environ, 'CODE_FLOW_START_VIEW': 'flow'})


if __name__ == '__main__':
    window = ReviewWindow()
    window.show_all()
    Gtk.main()
