"""Visible build stages; no guessed time estimate or install/analyse buttons."""
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango
from ReviewBuild import ReviewBuild


class ReviewBuildProgress(Gtk.Box):
    def __init__(self, cancel, back):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin=32)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.CENTER)
        self.set_hexpand(True)
        self.stage = 0
        self.active = False
        self.cancel, self.back = cancel, back
        self.title = Gtk.Label(label='Building diff map', xalign=0)
        self.title.get_style_context().add_class('review-title')
        self.pack_start(self.title, False, False, 0)
        self.bar = Gtk.ProgressBar(show_text=True)
        self.bar.set_tooltip_text('Progress through the build steps, not an estimate of remaining time.')
        self.pack_start(self.bar, False, False, 0)
        self.steps = []
        for name in ReviewBuild.STEPS:
            label = Gtk.Label(label=name, xalign=0)
            self.steps.append(label)
            self.pack_start(label, False, False, 0)
        row = Gtk.Box(spacing=8)
        self.spinner = Gtk.Spinner()
        row.pack_start(self.spinner, False, False, 0)
        self.detail = Gtk.Label(xalign=0, selectable=True)
        self.detail.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.detail.set_max_width_chars(90)
        row.pack_start(self.detail, True, True, 0)
        self.pack_start(row, False, False, 0)
        self.button = Gtk.Button(label='Cancel build')
        self.button.set_halign(Gtk.Align.START)
        self.button.connect('clicked', lambda *_: self.cancel() if self.active else self.back())
        self.pack_start(self.button, False, False, 0)
        self.connect('destroy', lambda *_: self.spinner.stop())

    def start(self):
        self.active = True
        self.title.set_text('Building diff map')
        self.button.set_label('Cancel build')
        self.button.set_sensitive(True)
        self.spinner.start()
        self.update(0, 'Preparing the review clone…')

    def update(self, stage, detail, fraction=0):
        self.stage = stage
        for index, (label, name) in enumerate(zip(self.steps, ReviewBuild.STEPS)):
            marker = '✓' if index < stage else '→' if index == stage else '○'
            label.set_text(f'{marker} {index + 1}. {name}')
        self.bar.set_fraction((stage + max(0, min(1, fraction))) / len(self.steps))
        self.bar.set_text(f'Step {stage + 1} of {len(self.steps)} · {ReviewBuild.STEPS[stage]}')
        self.detail.set_text(detail)
        self.detail.set_tooltip_text(detail)
        self.spinner.start()

    def cancelling(self):
        self.button.set_sensitive(False)
        self.detail.set_text('Cancelling… Waiting for the current process to stop.')

    def failed(self, message):
        self.active = False
        self.spinner.stop()
        self.title.set_text('Build stopped · ' + ReviewBuild.STEPS[self.stage])
        self.steps[self.stage].set_text(f'× {self.stage + 1}. {ReviewBuild.STEPS[self.stage]}')
        self.detail.set_text(message)
        self.detail.set_tooltip_text(message)
        self.button.set_label('Back to overview')
        self.button.set_sensitive(True)

    def finish(self):
        self.active = False
        self.spinner.stop()
        self.bar.set_fraction(1)
        self.bar.set_text('Diff map ready')
        for index, (label, name) in enumerate(zip(self.steps, ReviewBuild.STEPS)):
            label.set_text(f'✓ {index + 1}. {name}')
