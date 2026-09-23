"""Read-only GitHub conversations and local hide/unhide controls."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Pango


COMMENT_FIELDS = 'id body createdAt url author { login }'


def graphql(query):
    result = subprocess.run(['gh', 'api', 'graphql', '-f', 'query=' + query],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    payload = json.loads(result.stdout)
    if payload.get('errors'):
        raise RuntimeError('; '.join(error['message'] for error in payload['errors']))
    return payload['data']


def connection(root, field, fields, request=graphql):
    """Read every page, including replies beyond GitHub's first 100."""
    cursor = None
    while True:
        after = ', after: ' + json.dumps(cursor) if cursor else ''
        query = ('query { ' + root + ' { ' + field + '(first:100' + after + ') { '
                 'nodes { ' + fields + ' } pageInfo { hasNextPage endCursor } } } }')
        data = request(query)
        # Root aliases keep both PR and node queries at the same shape.
        page = data['root'][field]
        yield from page['nodes']
        if not page['pageInfo']['hasNextPage']:
            break
        cursor = page['pageInfo']['endCursor']


def load_conversations(number):
    # Resolve the PR node once so every paginated connection has the same root.
    data = graphql('query { repository(owner:"plancraft", name:"plancraft") { '
                   'pullRequest(number:' + str(int(number)) + ') { id } } }')
    node = data['repository']['pullRequest']['id']
    root = 'root: node(id:' + json.dumps(node) + ') { ... on PullRequest'
    # connection supplies the last two braces for the node + inline fragment.
    def pages(field, fields):
        return connection(root, field, fields, lambda query: graphql(query + '}'))
    items = []
    for field in ('comments', 'reviews'):
        for comment in pages(field, COMMENT_FIELDS):
            if comment['body'].strip():
                items.append(dict(id=comment['id'], title='Review' if field == 'reviews' else 'PR comment',
                                  comments=[comment]))
    for thread in pages('reviewThreads', 'id path line originalLine isResolved isOutdated'):
        thread_root = 'root: node(id:' + json.dumps(thread['id']) + ') { ... on PullRequestReviewThread'
        comments = list(connection(thread_root, 'comments', COMMENT_FIELDS,
                                   lambda query: graphql(query + '}')))
        line = thread['line'] or thread['originalLine']
        title = thread['path'] + (':' + str(line) if line else '')
        if thread['isResolved']:
            title += ' · Resolved on GitHub'
        if thread['isOutdated']:
            title += ' · Outdated'
        items.append(dict(id=thread['id'], title=title, comments=comments))
    return sorted(items, key=lambda item: item['comments'][0]['createdAt'] if item['comments'] else '')


class Conversations(Gtk.Box):
    def __init__(self, database=None, loader=load_conversations):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=8)
        if database is None:
            directory = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'code-flow'
            directory.mkdir(parents=True, exist_ok=True)
            database = directory / 'conversations.sqlite3'
        self.database = sqlite3.connect(str(database))
        self.database.execute('CREATE TABLE IF NOT EXISTS hidden (pr INTEGER, id TEXT, PRIMARY KEY(pr,id))')
        self.loader = loader
        self.number = None
        self.items = []
        self.generation = 0
        self.dead = False
        self.connect('destroy', self.close)
        refresh = Gtk.Button(label='Refresh conversations')
        refresh.connect('clicked', lambda *_: self.configure(self.number))
        self.pack_start(refresh, False, False, 0)
        self.message = Gtk.Label(label='Select a PR to see conversations.', xalign=0)
        self.message.set_line_wrap(True)
        self.pack_start(self.message, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.add(self.rows)
        self.pack_start(scroll, True, True, 0)

    def close(self, *_):
        self.dead = True
        self.database.close()

    def configure(self, number):
        self.number = number
        self.generation += 1
        generation = self.generation
        self.items = []
        self.render()
        self.message.set_text('Loading conversations…' if number else 'Select a PR to see conversations.')
        if number is None:
            return
        def run():
            try:
                items, error = self.loader(number), None
            except Exception as exception:
                items, error = [], str(exception)
            GLib.idle_add(self.loaded, generation, items, error)
        threading.Thread(target=run, daemon=True).start()

    def loaded(self, generation, items, error):
        if self.dead or generation != self.generation:
            return False
        self.items = items
        self.message.set_text('Could not load conversations: ' + error if error else
                              f'{len(items)} conversations · Hiding only affects this tool.')
        self.render()
        return False

    def set_hidden(self, item, hidden):
        try:
            with self.database:
                if hidden:
                    self.database.execute('INSERT OR IGNORE INTO hidden VALUES (?,?)', (self.number, item['id']))
                else:
                    self.database.execute('DELETE FROM hidden WHERE pr=? AND id=?', (self.number, item['id']))
        except sqlite3.Error as error:
            self.message.set_text('Could not save hidden state: ' + str(error))
            return
        self.render()

    def row(self, item, hidden):
        row = Gtk.Box(spacing=4)
        comments = item['comments']
        author = (comments[0].get('author') or {}).get('login', 'Deleted user') if comments else 'Unknown'
        expander = Gtk.Expander()
        label = Gtk.Label(label=f'{author} · {item["title"]} · {len(comments)} comments', xalign=0)
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        label.set_tooltip_text(label.get_text())
        expander.set_label_widget(label)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=8)
        for comment in comments:
            author = (comment.get('author') or {}).get('login', 'Deleted user')
            text = Gtk.Label(label=author + ' · ' + comment['createdAt'] + '\n\n' + comment['body'], xalign=0)
            text.set_selectable(True)
            text.set_line_wrap(True)
            text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            text.set_max_width_chars(45)
            body.pack_start(text, False, False, 0)
            body.pack_start(Gtk.LinkButton.new_with_label(comment['url'], 'View on GitHub'), False, False, 0)
        expander.add(body)
        row.pack_start(expander, True, True, 0)
        button = Gtk.Button(label='Unhide' if hidden else 'Hide')
        button.set_valign(Gtk.Align.START)
        button.connect('clicked', lambda *_: self.set_hidden(item, not hidden))
        row.pack_start(button, False, False, 0)
        return row

    def render(self):
        for child in self.rows.get_children():
            child.destroy()
        hidden = {row[0] for row in self.database.execute('SELECT id FROM hidden WHERE pr=?', (self.number,))}
        hidden_items = []
        for item in self.items:
            if item['id'] in hidden:
                hidden_items.append(item)
            else:
                self.rows.pack_start(self.row(item, False), False, False, 0)
        if hidden_items:
            group = Gtk.Expander(label=f'{len(hidden_items)} conversations hidden')
            rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for item in hidden_items:
                rows.pack_start(self.row(item, True), False, False, 0)
            group.add(rows)
            self.rows.pack_start(group, False, False, 0)
        self.rows.show_all()
