#!/usr/bin/env python3
"""Review map. Checkout, installation, and analysis run outside the GTK loop."""
import os
import json
import re
import subprocess
import sys
import threading
import json
import os
from pathlib import Path
from review_summary import checks_summary, purpose_excerpt
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Pango, Gio

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
        self.build = None
        self.destroyed = False
        self.connect('destroy', self.stop_review_build)
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
        self.home_button = Gtk.Button(label='⌂')
        self.home_button.set_tooltip_text('Back to recent reviews')
        self.home_button.connect('clicked', self.reset_selection)
        self.home_button.hide()
        bar.pack_start(self.home_button, False, False, 0)
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
        self.recent_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=24)
        self.recent_panel.set_halign(Gtk.Align.CENTER)
        self.recent_panel.set_valign(Gtk.Align.START)
        self.canvas.pack_start(self.recent_panel, True, True, 0)
        self.recent_panel.hide()
        self.views = Gtk.Stack()
        self.views.add_named(scroll, 'overview')
        from ReviewBuildProgress import ReviewBuildProgress
        self.build_progress = ReviewBuildProgress(self.cancel_review_build,
            lambda: self.show_overview((self.overview_branch, self.overview_pr)))
        self.views.add_named(self.build_progress, 'loading')
        from chat_panel import ChatPanel
        self.review_head = None
        self.chat = ChatPanel(ROOT, self.chat_context, self.chat_busy)
        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        split.pack1(self.views, resize=True, shrink=False)
        self.side_panel = Gtk.Notebook()
        self.side_panel.get_style_context().add_class('review-sidebar')
        self.side_panel.append_page(self.chat, Gtk.Label(label='Chat'))
        self.file_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        file_scroll = Gtk.ScrolledWindow()
        file_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        file_scroll.set_size_request(320, -1)
        file_scroll.add(self.file_list)
        self.side_panel.append_page(file_scroll, Gtk.Label(label='Files'))
        from conversations import Conversations
        from notes import Notes
        self.conversations = Conversations()
        self.notes = Notes()
        self.side_panel.append_page(self.conversations, Gtk.Label(label='Conversations'))
        self.side_panel.append_page(self.notes, Gtk.Label(label='Notes'))
        self.side_panel.connect('switch-page', self.show_conversations)
        split.pack2(self.side_panel, resize=False, shrink=False)
        split.set_position(950)
        box.pack_start(split, True, True, 0)
        self.connect('destroy', lambda *_: self.chat.client.stop() if self.chat.client else None)
        self.connect('key-press-event', self.review_key)
        self.recent_file = Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state')) / 'code-flow' / 'recent-selections.json'
        self.recent_file.parent.mkdir(parents=True, exist_ok=True)
        self.refresh_recent()
        self.refresh(fetch=False)

    def recent_selections(self):
        try:
            return json.loads(self.recent_file.read_text())
        except (OSError, ValueError):
            return []

    def remember_selection(self, mode, value, title):
        items = [item for item in self.recent_selections()
                 if not (item.get('mode') == mode and item.get('value') == value)]
        items.insert(0, {'mode': mode, 'value': value, 'title': title})
        self.recent_file.write_text(json.dumps(items[:8], indent=2))
        self.refresh_recent()

    def refresh_recent(self):
        for child in self.recent_panel.get_children():
            child.destroy()
        items = self.recent_selections()
        if not items:
            self.recent_panel.hide()
            return
        self.recent_panel.pack_start(Gtk.Label(label='Recent reviews', xalign=0), False, False, 0)
        for item in items:
            button = Gtk.Button(label=f"{item['mode'].title()} · {item['title']}")
            button.set_halign(Gtk.Align.FILL)
            button.connect('clicked', self.open_recent, item)
            self.recent_panel.pack_start(button, False, False, 0)
        self.recent_panel.show_all()

    def open_recent(self, _, item):
        self.mode.set_active(1 if item['mode'] == 'pr' else 0)
        field = self.pr if item['mode'] == 'pr' else self.branch
        field.set_text(item['value'])
        self.load_overview()

    def reset_selection(self, *_):
        self.branch_menu.popdown()
        self.clear_canvas()
        self.canvas.pack_start(self.recent_panel, True, True, 0)
        self.refresh_recent()
        self.canvas.show_all()
        self.branch.set_text('')
        self.pr.set_text('')
        self.base.set_text('')
        self.overview_pr = None
        self.overview_branch = None
        self.review_head = None
        self.actions.hide()
        self.home_button.hide()
        self.status.set_text('Select a branch or PR to load its overview.')
        self.chat.configure(None)
        self.conversations.configure(None)
        self.notes.configure(None)
        self.side_panel.set_current_page(0)

    def review_key(self, _, event):
        if event.keyval in (Gdk.KEY_f, Gdk.KEY_F) and event.state & Gdk.ModifierType.CONTROL_MASK:
            if self.views.get_visible_child_name() == 'map':
                self.views.get_child_by_name('map').open_search()
                return True
        if event.keyval == Gdk.KEY_Escape and self.views.get_visible_child_name() == 'map':
            board = self.views.get_child_by_name('map')
            if board.dependencies.selected is not None:
                board.dependencies.select(None)
                return True
        if event.keyval not in (Gdk.KEY_v, Gdk.KEY_V):
            return False
        if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        focus = self.get_focus()
        if isinstance(focus, Gtk.Entry) or (isinstance(focus, Gtk.TextView) and focus.get_editable()):
            return False
        if self.views.get_visible_child_name() != 'map':
            return False
        board = self.views.get_child_by_name('map')
        if board.active_file is None:
            return False
        board.sticky_viewed.set_active(not board.sticky_viewed.get_active())
        return True

    def show_conversations(self, notebook, page, index):
        if page is self.conversations:
            metadata = self.overview_pr
            number = metadata['number'] if metadata else None
            if number != self.conversations.number:
                self.conversations.configure(number)
        elif page is self.notes:
            metadata = self.overview_pr
            number = metadata['number'] if metadata else None
            if number != self.notes.number:
                self.notes.configure(number)

    def toggle_review_view(self, *_):
        if self.views.get_visible_child_name() == 'map':
            self.show_overview((self.overview_branch, self.overview_pr))
        else:
            self.open_review()

    def chat_busy(self, busy):
        blocked = busy or self.build is not None
        self.bar.set_sensitive(not blocked)
        self.actions.set_sensitive(not blocked)

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
        self.conversations.configure(None)
        self.notes.configure(None)
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

    def refresh_file_list(self):
        for child in self.file_list.get_children():
            child.destroy()
        board = self.views.get_child_by_name('map')
        if not board or self.views.get_visible_child_name() != 'map':
            self.file_list.pack_start(Gtk.Label(label='Open the diff map to browse files.'), False, False, 8)
        else:
            for file in sorted(board.files, key=lambda entry: entry[2]):
                label = Gtk.Label(label=('✓ ' if board.progress.is_viewed(file) else '○ ') + file[2], xalign=0)
                label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                button = Gtk.Button()
                button.get_style_context().add_class('file-row')
                button.add(label)
                button.set_tooltip_text(file[2])
                button.connect('clicked', lambda _, path=file[2]: (board.dependencies.select(path), board.jump_to_file(path)))
                self.file_list.pack_start(button, False, False, 0)
        self.file_list.show_all()

    def clear_canvas(self):
        board = self.views.get_child_by_name('map')
        if board and hasattr(board, 'dependencies'):
            board.dependencies.reset()
        self.views.set_visible_child_name('overview')
        self.refresh_recent()
        self.refresh_file_list()
        for child in self.canvas.get_children():
            self.canvas.remove(child)

    def show_overview(self, result):
        self.diff_button.set_label('Open diff map')
        branch, metadata = result
        self.overview_branch = branch
        self.overview_pr = metadata
        self.recent_panel.hide()
        self.home_button.show()
        self.remember_selection('pr' if metadata else 'branch',
                               str(metadata['number']) if metadata else branch,
                               metadata['title'] if metadata else branch)
        self.chat.configure(metadata['number'] if metadata else None)
        number = metadata['number'] if metadata else None
        if self.conversations.number != number:
            self.conversations.configure(None)
            if self.side_panel.get_current_page() == 2:
                self.conversations.configure(number)
        if self.notes.number != number:
            self.notes.configure(None)
            if self.side_panel.get_current_page() == 3:
                self.notes.configure(number)
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
        query = ''.join(self.branch.get_text().casefold().split())
        ranked = []
        for order, name in enumerate(self.branch_options):
            candidate = name.casefold()
            positions = []
            cursor = 0
            for char in query:
                position = candidate.find(char, cursor)
                if position < 0:
                    break
                positions.append(position)
                cursor = position + 1
            else:
                # Prefer literal matches, then matches with fewer skipped letters.
                gaps = positions[-1] - positions[0] + 1 - len(query) if positions else 0
                ranked.append(((query not in candidate, gaps, order), name))
        matches = [name for _, name in sorted(ranked)]
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
        if self.build is not None or self.chat.busy:
            return
        from ReviewBuild import ReviewBuild
        build = self.build = ReviewBuild(ROOT, self.overview_branch, self.base.get_text().strip(), self.overview_pr)
        previous = self.views.get_child_by_name('map')
        if previous:
            previous.destroy()
        self.review_head = None
        self.chat.set_sensitive(False)
        self.chat_busy(False)
        self.canvas.set_sensitive(False)
        self.build_progress.start()
        self.build_progress.show_all()
        self.views.set_visible_child_name('loading')
        self.status.set_text('Building diff map…')

        def deliver(callback, *args):
            def invoke():
                if self.build is build and not self.destroyed:
                    try:
                        callback(*args)
                    except Exception as error:
                        self.build_failed(build, str(error))
                return False
            GLib.idle_add(invoke)

        def progress(stage, detail, fraction=0):
            def update():
                if not build.dependency.cancelled.is_set():
                    self.build_progress.update(stage, detail, fraction)
            deliver(update)

        def run():
            try:
                result = build.run(progress)
            except Exception as error:
                deliver(lambda message: self.build_failed(build, message), str(error))
            else:
                deliver(self.show_review, result)
        threading.Thread(target=run, daemon=True).start()

    def cancel_review_build(self):
        if self.build is None:
            return
        self.build_progress.cancelling()
        self.build.cancel()
        board = self.views.get_child_by_name('map')
        if board and board.render_source is not None:
            board.cancel_render()
            self.build_failed(self.build, 'Build cancelled.')

    def stop_review_build(self, *_):
        self.destroyed = True
        if self.build is not None:
            self.build.cancel()
            self.build = None

    def build_failed(self, build, message):
        if self.build is not build:
            return
        self.build = None
        board = self.views.get_child_by_name('map')
        if board:
            board.destroy()
        self.build_progress.failed(message)
        self.status.set_text('Diff map not built: ' + message)
        self.chat.set_sensitive(True)
        self.chat_busy(self.chat.busy)
        self.canvas.set_sensitive(True)

    def open_dependency_source(self, head, path, line, column):
        def check():
            from review_git import ReviewGit
            repository = ReviewGit(ROOT)
            repository.ensure_clean()
            if repository.resolve_ref('HEAD') != head:
                raise ValueError('Review checkout changed. Reopen the map before opening dependency locations.')
        def done(_):
            if self.review_head == head:
                self.open_file_in_zed(path, line, column)
        self.work(check, done)

    def open_file_in_zed(self, path, line=None, column=None):
        file = (ROOT / path).resolve()
        if not file.is_relative_to(ROOT.resolve()) or not file.is_file():
            self.status.set_text('File is not available in the review checkout: ' + path)
            return
        def finished(process, result):
            try:
                process.wait_check_finish(result)
            except GLib.Error as error:
                self.status.set_text('Could not open Zed: ' + str(error))
        try:
            location = str(file) + (f':{max(1, int(line))}:{max(1, int(column or 1))}' if line is not None else '')
            process = Gio.Subprocess.new(['zed', str(ROOT), location], Gio.SubprocessFlags.NONE)
            process.wait_check_async(None, finished)
        except GLib.Error as error:
            self.status.set_text('Could not open Zed: ' + str(error))

    def show_review(self, review):
        from diff_canvas import DiffCanvas
        build = self.build
        build.check_cancelled()
        merge, head, files = review['merge'], review['head'], review['files']
        scope = (f'plancraft/plancraft:pr:{self.overview_pr["number"]}' if self.overview_pr
                 else f'{ROOT}:branch:{self.overview_branch}:base:{self.base.get_text()}')
        board = DiffCanvas(files, review_scope=scope, head=head, file_commits=review['file_commits'],
                           open_file=self.open_file_in_zed, defer_render=True,
                           open_source=lambda path, line, column: self.open_dependency_source(head, path, line, column))
        self.views.add_named(board, 'map')
        board.show_all()
        def progress(count, total, path):
            build.check_cancelled()
            self.build_progress.update(4, f'{count} / {total} file cards · {path}', count / max(1, total))
        def ready():
            if self.build is not build or self.destroyed:
                return
            build.check_cancelled()
            if review['links'] is not None:
                board.dependencies.accept(review['links'])
            elif review['links_error']:
                board.dependencies.unavailable(review['links_error'])
            self.review_head = head
            self.views.set_visible_child_name('map')
            board.on_viewed_changed = self.refresh_file_list
            self.refresh_file_list()
            self.diff_button.set_label('Back to overview')
            message = f'{len(files)} files · {merge[:10]} → {head[:10]} · Diff map'
            if review['links_error']:
                message += ' · Links unavailable (see map notice)'
            self.status.set_text(message)
            self.build_progress.finish()
            self.build = None
            self.chat.set_sensitive(True)
            self.chat_busy(self.chat.busy)
            self.canvas.set_sensitive(True)
        board.render_incrementally(progress, ready, lambda message: self.build_failed(build, message))

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
        subprocess.Popen([sys.executable, str(Path(__file__).with_name('code-flow.py'))], env={**os.environ, 'CODE_FLOW_START_VIEW': 'flow'})


if __name__ == '__main__':
    window = ReviewWindow()
    window.show_all()
    Gtk.main()
