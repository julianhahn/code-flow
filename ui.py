"""Small widget library. Build UI through these so styling stays in theme.py.

style(widget, 'muted')   add theme classes; an unknown class raises ValueError (never silent)
button / label / badge   ready-made widgets with a role instead of ad-hoc classes
"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from theme import CLASSES, STATUS, T

BADGE_TONES = ('good', 'attention', 'neutral')
LABEL_ROLES = (None, 'muted', 'eyebrow', 'title', 'section', 'purpose', 'code', 'keycap')
_ROLE_CLASS = {'title': 'review-title', 'section': 'section-title', 'code': 'code-text'}


def style(widget, *names):
    for name in names:
        if name not in CLASSES:
            raise ValueError(f'Unknown style class {name!r}; add it to theme.py')
        widget.get_style_context().add_class(name)
    return widget


def button(text, primary=False, tooltip=None, on_click=None):
    widget = Gtk.Button(label=text)
    if primary:
        style(widget, 'primary')
    if tooltip:
        widget.set_tooltip_text(tooltip)
    if on_click:
        widget.connect('clicked', on_click)
    return widget


def label(text, role=None, selectable=False, wrap=False):
    if role not in LABEL_ROLES:
        raise ValueError(f'Unknown label role {role!r}; use one of {LABEL_ROLES}')
    widget = Gtk.Label(label=text, xalign=0, selectable=selectable)
    widget.set_line_wrap(wrap)
    if role:
        style(widget, _ROLE_CLASS.get(role, role))
    return widget


def badge(text, tone='neutral'):
    if tone not in BADGE_TONES:
        raise ValueError(f'Unknown badge tone {tone!r}; use one of {BADGE_TONES}')
    widget = label(text, selectable=True, wrap=True)
    widget.set_halign(Gtk.Align.START)
    return style(widget, 'badge', tone)


def status_colors(status):
    """(background, foreground) hex for a git status letter such as 'M' or 'A'."""
    bg, fg = STATUS.get(status[:1], STATUS['M'])
    return T[bg], T[fg]


def text_tags(buffer, kinds):
    """Create diff text tags. kinds: {tag name: (background token, foreground token)}."""
    for name, (bg, fg) in kinds.items():
        buffer.create_tag(name, paragraph_background=T[bg], foreground=T[fg])
