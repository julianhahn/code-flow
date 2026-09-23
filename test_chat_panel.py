import unittest
from pathlib import Path
from chat_panel import ChatPanel, Gtk


class ChatPanelTests(unittest.TestCase):
    def test_context_height_stays_fixed_for_long_paths(self):
        panel = ChatPanel(Path('/tmp'), lambda: {}, lambda _: None)
        window = Gtk.Window()
        window.set_default_size(440, 600)
        window.add(panel)
        window.show_all()
        try:
            heights = []
            for text in ('PR #1', 'PR #1 · abc\n' + 'very/long/path/' * 30 + '\nAttached: diff rows 1–20'):
                panel.context_label.set_text(text)
                while Gtk.events_pending():
                    Gtk.main_iteration()
                heights.append(panel.context_label.get_preferred_height_for_width(390))
                self.assertEqual(panel.context_label.get_tooltip_text(), text)
            self.assertEqual(heights[0], heights[1])
        finally:
            window.destroy()

    def test_roles_code_blocks_and_streaming(self):
        panel = ChatPanel(Path('/tmp'), lambda: {}, lambda _: None)
        try:
            context = {'head': 'abc', 'file': 'test.py'}
            panel.event('user', 'Explain this', context)
            panel.event('delta', 'Here is code:\n```python\nprint(1)', context)
            panel.event('delta', '\n```\nDone.', context)
            cards = panel.messages.get_children()
            self.assertEqual(len(cards), 2)
            self.assertTrue(cards[0].get_style_context().has_class('chat-user'))
            self.assertTrue(cards[1].get_style_context().has_class('chat-agent'))
            body = cards[1].get_children()[1]
            self.assertEqual(len(body.get_children()), 3)
            scroll = body.get_children()[1]
            block = scroll.get_child().get_child()
            self.assertEqual(block.get_children()[1].get_text(), 'print(1)')
            panel.render_message(body, '<unsafe> & literal')
            self.assertEqual(body.get_children()[0].get_text(), '<unsafe> & literal')
        finally:
            panel.destroy()
