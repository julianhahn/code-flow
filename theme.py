"""The only place that defines colors. Everything else uses token names.

Why this shape (so a theme bug is loud or impossible):
- TOKENS is the whole palette. CSS is a string.Template over it: a typo'd token raises KeyError.
- The base rules (`*`) give every widget, including popups, menus and dialogs, a readable
  foreground. Nothing relies on a parent widget being styled.
- CLASSES is read out of the CSS itself, and ui.style() rejects unknown classes.
- PAIRS lists every text-on-background combination; test_theme.py fails below WCAG AA.
- test_theme.py also fails if any other file contains a hex color.
"""
import re
from string import Template

TOKENS = {
    # surfaces
    'bg': '#0d1117', 'panel': '#161b22', 'raised': '#21262d', 'hover': '#30363d',
    'line': '#30363d', 'line_strong': '#484f58',
    # text
    'text': '#e6edf3', 'text_dim': '#c9d1d9', 'muted': '#8b949e',
    # accent
    'accent': '#58a6ff', 'accent_solid': '#1f6feb', 'accent_hover': '#388bfd',
    'on_accent': '#ffffff', 'active_tab': '#f78166',
    # status
    'good': '#3fb950', 'good_bg': '#12261a', 'good_line': '#238636',
    'warn': '#e3b341', 'warn_bg': '#2b2310', 'warn_line': '#9e6a03',
    'bad': '#ff7b72', 'bad_bg': '#2d1519', 'purple': '#bc8cff',
    # chat
    'user_bg': '#12233d', 'user_line': '#1f4272',
    # diff text
    'add_bg': '#12261e', 'add_fg': '#aff5b4', 'del_bg': '#2d1519', 'del_fg': '#ffdcd7',
    'hunk_bg': '#121d2f', 'hunk_fg': '#79c0ff',
    'hit_bg': '#7a5c00', 'hit_fg': '#ffffff', 'current_bg': '#d29922', 'current_fg': '#0d1117',
    # diff map
    'connector': '#484f58', 'node': '#9da7b3',
}
T = TOKENS

# Text that is drawn on a background somewhere in the app: (foreground token, background token).
PAIRS = [
    ('text', 'bg'), ('text', 'panel'), ('text', 'raised'), ('text_dim', 'bg'), ('text_dim', 'panel'),
    ('text_dim', 'raised'), ('muted', 'bg'), ('muted', 'panel'), ('muted', 'raised'),
    ('accent', 'bg'), ('accent', 'raised'), ('on_accent', 'accent_solid'),
    ('good', 'good_bg'), ('warn', 'warn_bg'), ('bad', 'bad_bg'), ('good', 'bg'), ('bad', 'bg'),
    ('text', 'user_bg'), ('add_fg', 'add_bg'), ('del_fg', 'del_bg'), ('hunk_fg', 'hunk_bg'),
    ('hit_fg', 'hit_bg'), ('current_fg', 'current_bg'), ('good', 'panel'), ('warn', 'panel'),
    ('node', 'panel')
]

# Diff-map card header colors by git status letter: (background, foreground).
STATUS = {'A': ('good_bg', 'good'), 'D': ('bad_bg', 'bad'), 'M': ('warn_bg', 'warn')}

_CSS = Template('''
* { color: $text_dim; font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace; }
*:disabled, *:disabled label { color: $muted; }
window, dialog, .background { background-color: $bg; color: $text; }
popover, menu, tooltip { background-color: $panel; border: 1px solid $line; border-radius: 8px; }
menuitem, menuitem label, modelbutton, modelbutton label { color: $text; background-color: transparent; }
menuitem:hover, modelbutton:hover { background-color: $hover; }
list, row { background-color: $panel; color: $text_dim; }
row:hover { background-color: $raised; }
row:selected, row:selected label { background-color: $accent_solid; color: $on_accent; }
selection, *:selected, text selection { background-color: $accent_solid; color: $on_accent; }
scrolledwindow, viewport, .view, notebook, notebook > stack, notebook > stack > * { background-color: $bg; }
textview, textview text { background-color: $bg; color: $text; }
scrollbar { background: transparent; }
scrollbar slider { background-color: $line; border-radius: 6px; min-width: 8px; min-height: 8px; }
scrollbar slider:hover { background-color: $line_strong; }
paned > separator { background-color: $line; min-width: 1px; }

entry, button, combobox button { border-radius: 6px; min-height: 28px; }
entry { background-color: $bg; color: $text; border: 1px solid $line; padding: 4px 10px; caret-color: $accent; }
entry:focus { border-color: $accent; box-shadow: 0 0 0 1px $accent; }
entry placeholder { color: $muted; }
button { background-image: none; background-color: $raised; color: $text_dim; border: 1px solid $line; padding: 4px 12px; box-shadow: none; text-shadow: none; }
button label { color: inherit; }
button:hover { background-color: $hover; border-color: $line_strong; }
button.primary { background-color: $accent_solid; border-color: $accent_hover; color: $on_accent; font-weight: bold; padding: 6px 18px; }
button.primary:hover { background-color: $accent_hover; }
button:disabled { opacity: 0.5; }
button.link, button.link label { color: $accent; }
checkbutton check { background-image: none; background-color: $bg; border: 1px solid $line_strong; border-radius: 4px; color: $on_accent; }
checkbutton check:checked { background-color: $accent_solid; border-color: $accent_hover; }

.overview-card { background-color: $panel; border: 1px solid $line; border-radius: 10px; padding: 28px; }
.eyebrow { color: $accent; font-size: 9pt; font-weight: bold; letter-spacing: 2px; }
.review-title { font-size: 20pt; font-weight: bold; color: $text; margin-bottom: 8px; }
.muted { color: $muted; font-size: 9pt; }
.badge { border-radius: 999px; padding: 4px 12px; font-size: 9pt; font-weight: bold; border: 1px solid transparent; }
.good { background-color: $good_bg; color: $good; border-color: $good_line; }
.attention { background-color: $warn_bg; color: $warn; border-color: $warn_line; }
.neutral { background-color: $raised; color: $text_dim; border-color: $line; }
.stats { background-color: $bg; border: 1px solid $line; border-radius: 8px; padding: 12px 14px; font-size: 11pt; font-weight: bold; margin-top: 8px; }
label.stat-added { color: $good; }
label.stat-removed { color: $bad; }
label.stat-files { color: $text_dim; }
.purpose { font-size: 13pt; color: $text_dim; margin-top: 2px; margin-bottom: 12px; }
.section-title { font-size: 9pt; color: $muted; font-weight: bold; letter-spacing: 1px; margin-top: 16px; }
.file-row { padding: 8px 10px; }
.keycap { background-color: $raised; color: $text; border: 1px solid $line_strong; border-radius: 5px; padding: 2px 8px; font-weight: bold; }

.review-sidebar { background-color: $bg; border: 0; border-left: 1px solid $line; }
.review-sidebar header, notebook > header { background-color: $panel; border-bottom: 1px solid $line; }
.review-sidebar tab { padding: 6px 12px; border-bottom: 2px solid transparent; background-color: transparent; }
.review-sidebar tab label { color: $muted; }
.review-sidebar tab:hover label { color: $text; }
.review-sidebar tab:checked { border-bottom: 2px solid $active_tab; }
.review-sidebar tab:checked label { color: $text; font-weight: bold; }

.chat-user { background-color: $user_bg; border: 1px solid $user_line; border-radius: 8px; padding: 12px; margin-left: 20px; }
.chat-agent { background-color: $panel; border: 1px solid $line; border-radius: 8px; padding: 12px; margin-right: 8px; }
.chat-role { font-weight: bold; color: $accent; font-size: 9pt; letter-spacing: 1px; }
.chat-code { background-color: $bg; border: 1px solid $line; border-radius: 6px; padding: 10px; }
.code-text { font-size: 10pt; color: $text; }

.diff-card { border-color: $line; }
.diff-card.focused-card { border-color: $accent; }
.diff-card.dependency-selected { border-color: $purple; }
''')

CSS = _CSS.substitute(TOKENS)  # KeyError here = a token is missing: loud, at import time.
CLASSES = frozenset(re.findall(r'\.([a-z][a-z0-9-]*)', re.sub(r'\d+\.\d+', '', CSS)))


def _luminance(color):
    channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]


def contrast(foreground, background):
    """WCAG contrast ratio of two token names."""
    a, b = sorted((_luminance(T[foreground]), _luminance(T[background])), reverse=True)
    return (a + .05) / (b + .05)


def apply(screen):
    """Install the theme for every window of this process (popups and dialogs included)."""
    from gi.repository import Gtk
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode())  # GLib.Error on a syntax mistake: loud.
    Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    return provider
