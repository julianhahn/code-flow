//! Local rich-text notes, using the original table and GTK character offsets.
use crate::backend::{database, Result};
use crate::widgets::{button, display_text, hbox, label, scroll, vbox};
use gtk::prelude::*;
use rusqlite::{params, OptionalExtension};
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::Duration;

type Spans = Vec<(String, i32, i32)>;

fn notes_db() -> Result<rusqlite::Connection> {
    prepare_notes_db(database("notes.sqlite3")?)
}

fn prepare_notes_db(mut db: rusqlite::Connection) -> Result<rusqlite::Connection> {
    let tx = db
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
        .map_err(|e| e.to_string())?;
    tx.execute_batch("CREATE TABLE IF NOT EXISTS notes(pr INTEGER PRIMARY KEY,body TEXT NOT NULL,formatting TEXT NOT NULL DEFAULT '[]')").map_err(|e|e.to_string())?;
    let exists: bool = tx
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM pragma_table_info('notes') WHERE name='formatting')",
            [],
            |row| row.get(0),
        )
        .map_err(|e| e.to_string())?;
    if !exists {
        tx.execute_batch("ALTER TABLE notes ADD COLUMN formatting TEXT NOT NULL DEFAULT '[]'")
            .map_err(|e| e.to_string())?;
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(db)
}

fn read_note(db: &rusqlite::Connection, number: u64) -> Result<(String, Spans)> {
    let row = db
        .query_row(
            "SELECT body,formatting FROM notes WHERE pr=?",
            [number],
            |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
        )
        .optional()
        .map_err(|e| e.to_string())?;
    let Some((body, formatting)) = row else {
        return Ok((String::new(), Vec::new()));
    };
    let spans =
        serde_json::from_str(&formatting).map_err(|e| format!("Invalid note formatting: {e}"))?;
    Ok((body, spans))
}

fn save_note(db: &rusqlite::Connection, number: u64, body: &str, spans: &Spans) -> Result<()> {
    let serialized = serde_json::to_string(spans).map_err(|e| e.to_string())?;
    db.execute(
        "INSERT OR REPLACE INTO notes(pr,body,formatting) VALUES(?,?,?)",
        params![number, body, serialized],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

pub struct Notes {
    pub panel: gtk::Box,
    bar: gtk::Box,
    editor: gtk::TextView,
    buffer: gtk::TextBuffer,
    tags: Vec<(&'static str, gtk::TextTag)>,
    message: gtk::Label,
    number: Cell<Option<u64>>,
    timer: RefCell<Option<glib::SourceId>>,
    loading: Cell<bool>,
    dirty: Cell<bool>,
}

impl Notes {
    pub fn new() -> Rc<Self> {
        let panel = vbox(10);
        panel.set_margin(16);
        panel.pack_start(&label("Review notes", &[]), false, false, 0);
        let bar = hbox(6);
        panel.pack_start(&bar, false, false, 0);
        let editor = gtk::TextView::new();
        editor.set_wrap_mode(gtk::WrapMode::WordChar);
        editor.set_editable(false);
        bar.set_sensitive(false);
        editor.set_left_margin(16);
        editor.set_right_margin(16);
        editor.set_top_margin(16);
        editor.set_bottom_margin(16);
        editor.set_pixels_below_lines(5);
        let buffer = editor.buffer().unwrap();
        let tags = ["bold", "italic", "underline"]
            .iter()
            .map(|&name| {
                let tag = gtk::TextTag::new(Some(name));
                match name {
                    "bold" => tag.set_weight(700),
                    "italic" => tag.set_style(pango::Style::Italic),
                    _ => tag.set_underline(pango::Underline::Single),
                }
                buffer.tag_table().unwrap().add(&tag);
                (name, tag)
            })
            .collect();
        panel.pack_start(&scroll(&editor), true, true, 0);
        let message = label("Select a PR to write notes.", &["muted"]);
        message.set_line_wrap(true);
        panel.pack_start(&message, false, false, 0);
        let notes = Rc::new(Self {
            panel,
            bar,
            editor,
            buffer,
            tags,
            message,
            number: Cell::new(None),
            timer: Default::default(),
            loading: Cell::new(false),
            dirty: Cell::new(false),
        });
        for (name, caption, key) in [
            ("bold", "Bold", "B"),
            ("italic", "Italic", "I"),
            ("underline", "Underline", "U"),
        ] {
            let b = button(caption);
            b.set_focus_on_click(false);
            b.set_tooltip_text(Some(&format!("Select text · Ctrl+{key}")));
            let weak = Rc::downgrade(&notes);
            b.connect_clicked(move |_| {
                if let Some(notes) = weak.upgrade() {
                    notes.format(name)
                }
            });
            notes.bar.pack_start(&b, false, false, 0);
        }
        let weak = Rc::downgrade(&notes);
        notes.buffer.connect_changed(move |_| {
            if let Some(notes) = weak.upgrade() {
                notes.changed()
            }
        });
        let weak = Rc::downgrade(&notes);
        notes.editor.connect_key_press_event(move |_, event| {
            let state = event.state();
            let name = if state.contains(gdk::ModifierType::CONTROL_MASK)
                && !state.intersects(gdk::ModifierType::MOD1_MASK | gdk::ModifierType::SUPER_MASK)
            {
                match event.keyval().to_unicode().map(|c| c.to_ascii_lowercase()) {
                    Some('b') => Some("bold"),
                    Some('i') => Some("italic"),
                    Some('u') => Some("underline"),
                    _ => None,
                }
            } else {
                None
            };
            if let (Some(notes), Some(name)) = (weak.upgrade(), name) {
                notes.format(name);
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        notes
    }

    fn format(self: &Rc<Self>, name: &str) {
        if self.number.get().is_none() {
            return;
        }
        let Some((start, end)) = self.buffer.selection_bounds() else {
            self.message.set_text("Select text to format it.");
            return;
        };
        let tag = &self.tags.iter().find(|(n, _)| *n == name).unwrap().1;
        // A selection is uniformly tagged when no toggle occurs before its end.
        let mut next = start;
        let all = start.has_tag(tag)
            && (!next.forward_to_tag_toggle(Some(tag)) || next.offset() >= end.offset());
        if all {
            self.buffer.remove_tag(tag, &start, &end)
        } else {
            self.buffer.apply_tag(tag, &start, &end)
        }
        self.changed();
    }

    fn changed(self: &Rc<Self>) {
        if self.loading.get() || self.number.get().is_none() {
            return;
        }
        self.dirty.set(true);
        self.message.set_text("Saving…");
        if self.timer.borrow().is_some() {
            return;
        }
        let weak = Rc::downgrade(self);
        *self.timer.borrow_mut() = Some(glib::timeout_add_local_once(
            Duration::from_millis(300),
            move || {
                if let Some(notes) = weak.upgrade() {
                    notes.timer.borrow_mut().take();
                    if let Err(e) = notes.flush() {
                        eprintln!("{e}");
                    }
                }
            },
        ));
    }

    fn spans(&self) -> Spans {
        let mut spans = Vec::new();
        for (name, tag) in &self.tags {
            let mut at = self.buffer.start_iter();
            while !at.is_end() {
                let start = at.offset();
                let used = at.has_tag(tag);
                if !at.forward_to_tag_toggle(Some(tag)) {
                    at = self.buffer.end_iter();
                }
                if used && at.offset() > start {
                    spans.push((name.to_string(), start, at.offset()));
                }
            }
        }
        spans
    }

    pub fn flush(&self) -> Result<()> {
        if let Some(id) = self.timer.borrow_mut().take() {
            id.remove();
        }
        let Some(number) = self.number.get().filter(|_| self.dirty.get()) else {
            return Ok(());
        };
        let body = self
            .buffer
            .text(&self.buffer.start_iter(), &self.buffer.end_iter(), true)
            .unwrap_or_default();
        let save = notes_db().and_then(|db| save_note(&db, number, body.as_str(), &self.spans()));
        if let Err(e) = save {
            let msg = format!("Could not save notes: {e}");
            self.message.set_text(&msg);
            return Err(msg);
        }
        self.dirty.set(false);
        self.message.set_text("Saved locally · Kept across commits");
        Ok(())
    }

    pub fn configure(&self, number: Option<u64>) -> Result<()> {
        if self.number.get() == number {
            return Ok(());
        }
        self.flush()?;
        let (body, spans) = if let Some(n) = number {
            read_note(&notes_db()?, n)?
        } else {
            (String::new(), Vec::new())
        };
        self.loading.set(true);
        self.buffer.set_text(&display_text(&body));
        for (name, start, end) in spans {
            if start >= 0
                && end > start
                && end <= self.buffer.char_count()
                && self.tags.iter().any(|(tag, _)| *tag == name)
            {
                self.buffer.apply_tag_by_name(
                    &name,
                    &self.buffer.iter_at_offset(start),
                    &self.buffer.iter_at_offset(end),
                );
            }
        }
        self.number.set(number);
        self.loading.set(false);
        self.editor.set_editable(number.is_some());
        self.bar.set_sensitive(number.is_some());
        self.message.set_text(if number.is_some() {
            "Saved locally · Kept across commits"
        } else {
            "Select a PR to write notes."
        });
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn old_notes_and_overlapping_character_spans_survive_a_restart() {
        let temp = tempfile::tempdir().unwrap();
        let file = temp.path().join("notes.sqlite3");
        let old = rusqlite::Connection::open(&file).unwrap();
        old.execute_batch("CREATE TABLE notes(pr INTEGER PRIMARY KEY,body TEXT NOT NULL); INSERT INTO notes VALUES(2,'legacy')").unwrap();
        let db = prepare_notes_db(old).unwrap();
        assert_eq!(read_note(&db, 2).unwrap(), ("legacy".into(), Vec::new()));
        let spans = vec![
            ("bold".into(), 0, 2),
            ("italic".into(), 1, 3),
            ("underline".into(), 3, 4),
        ];
        save_note(&db, 7, "猫😀ab", &spans).unwrap();
        drop(db);
        let reopened = prepare_notes_db(rusqlite::Connection::open(&file).unwrap()).unwrap();
        assert_eq!(read_note(&reopened, 7).unwrap(), ("猫😀ab".into(), spans));
        assert_eq!(read_note(&reopened, 2).unwrap().0, "legacy");
        assert_eq!(read_note(&reopened, 10).unwrap().0, "");
    }
}
