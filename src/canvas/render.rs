use super::*;
use crate::theme;
use std::{
    collections::BTreeMap,
    time::{Duration, Instant},
};

const PRIORITY: u32 = gtk::STYLE_PROVIDER_PRIORITY_APPLICATION + 1;

fn round(v: f64) -> i32 {
    v.round_ties_even() as i32
}

fn status_colors(status: &str) -> (&'static str, &'static str) {
    match status.as_bytes().first() {
        Some(b'A') => ("good_bg", "good"),
        Some(b'D') => ("bad_bg", "bad"),
        _ => ("warn_bg", "warn"),
    }
}

/// Providers are shared by widgets drawn at the same zoom level.
struct Styles {
    css: BTreeMap<(&'static str, &'static str), gtk::CssProvider>,
    controls: BTreeMap<&'static str, gtk::CssProvider>,
    border: gtk::CssProvider,
    font: pango::FontDescription,
}
impl Styles {
    fn new(z: f64) -> Self {
        let points = (11.0 * z).max(3.0);
        let mut font = pango::FontDescription::from_string("Monospace");
        font.set_size(round(points * pango::SCALE as f64));
        let border = theme::provider(&format!(
            ".diff-card {{ border-style: solid; border-width: {}px; }}",
            round(3.0 * z).max(1)
        ));
        let mut css = BTreeMap::new();
        for (bg, fg) in [
            ("panel", "node"),
            ("good_bg", "good"),
            ("bad_bg", "bad"),
            ("warn_bg", "warn"),
            ("connector", "connector"),
            ("good", "good"),
            ("bad", "bad"),
            ("warn", "warn"),
            ("bg", "text_dim"),
        ] {
            css.insert(
                (bg, fg),
                theme::provider(&format!(
                    "* {{ background-color: @cf_{bg}; color: @cf_{fg}; font-size: {points}pt; }}"
                )),
            );
        }
        let mut controls = BTreeMap::new();
        for fg in ["good", "bad", "warn"] {
            let padding = round(3.0 * z).max(1);
            let check = round(12.0 * z).max(4);
            controls.insert(fg,theme::provider(&format!("
                * {{font-size: {points}pt;}}
                button, checkbutton {{
                    min-width: 0; min-height: 0; padding: {padding}px; margin: 0; border: 0; border-radius: 0;
                    background-image: none; background-color: transparent; box-shadow: none; color: @cf_{fg};
                }}
                button:hover {{background-color: alpha(@cf_{fg}, 0.12);}}
                check {{min-width: {check}px; min-height: {check}px; padding: 0; margin: 0;}}
            ")));
        }
        Self {
            css,
            controls,
            border,
            font,
        }
    }

    fn style(&self, w: &impl IsA<gtk::Widget>, bg: &'static str, fg: &'static str) {
        w.style_context()
            .add_provider(&self.css[&(bg, fg)], PRIORITY);
    }
    fn label(&self, text: &str, bg: &'static str, fg: &'static str, z: f64) -> gtk::Label {
        let label = gtk::Label::new(Some(text));
        label.set_xalign(0.0);
        label.set_margin_start(round(8.0 * z).max(2));
        label.set_margin_end(round(8.0 * z).max(2));
        self.style(&label, bg, fg);
        label
    }
}

struct Layout {
    order: Vec<(usize, usize)>,
    index: usize,
    styles: Styles,
    column: Option<usize>,
    column_x: f64,
    right: f64,
    y: f64,
    bottom: f64,
    folders: HashMap<String, (f64, f64)>,
}

impl Layout {
    fn new(c: &Canvas) -> Self {
        let z = c.zoom.get();
        let mut order: Vec<_> = c
            .files
            .iter()
            .enumerate()
            .filter(|(_, f)| !c.spec.is_active() || !is_spec(&f.path))
            .map(|(i, f)| {
                (
                    COLUMNS
                        .iter()
                        .position(|&name| name == group(&f.path))
                        .unwrap_or(4),
                    i,
                )
            })
            .collect();
        order.sort_by(|&(col, a), &(col2, b)| {
            col.cmp(&col2).then(c.files[a].path.cmp(&c.files[b].path))
        });
        Self {
            order,
            index: 0,
            styles: Styles::new(z),
            column: None,
            column_x: 40.0 * z,
            right: 0.0,
            y: 0.0,
            bottom: 800.0 * z,
            folders: HashMap::new(),
        }
    }
    fn draw_next(&mut self, c: &Rc<Canvas>) -> Option<usize> {
        let &(column, index) = self.order.get(self.index)?;
        let f = &c.files[index];
        let z = c.zoom.get();
        if self.column != Some(column) {
            self.folders.clear();
            if self.column.is_some() {
                self.column_x = self.right + 100.0 * z;
            }
            self.column = Some(column);
            self.y = 85.0 * z;
            self.right = self.column_x + 600.0 * z;
            c.put(
                &self
                    .styles
                    .label(&COLUMNS[column].to_uppercase(), "panel", "node", z),
                self.column_x,
                25.0 * z,
            );
        }
        let parts: Vec<_> = f.path.split('/').collect();
        let mut prefix = String::new();
        let mut parent = None;
        for (d, name) in parts.iter().take(parts.len() - 1).enumerate() {
            if !prefix.is_empty() {
                prefix.push('/');
            }
            prefix.push_str(name);
            let point = if let Some(existing) = self.folders.get(&prefix) {
                *existing
            } else {
                let x = self.column_x + d as f64 * 28.0 * z;
                if let Some(origin) = parent {
                    c.connector(&self.styles, origin, x, self.y + 10.0 * z, "connector");
                }
                let node = self.styles.label(name, "panel", "node", z);
                node.set_tooltip_text(Some(&prefix));
                let size = c.put(&node, x, self.y);
                let point = (x + 12.0 * z, self.y + size.1);
                self.folders.insert(prefix.clone(), point);
                self.y += size.1 + 18.0 * z;
                point
            };
            parent = Some(point);
        }
        let x = self.column_x + (parts.len() - 1) as f64 * 28.0 * z;
        let (frame, text) = c.make_card(index, &self.styles);
        if let Some(origin) = parent {
            c.connector(
                &self.styles,
                origin,
                x,
                self.y + 10.0 * z,
                status_colors(&f.status).1,
            );
        }
        let (w, h) = c.put(&frame, x, self.y);
        c.cards.borrow_mut().push(Card {
            index,
            rect: Rect { x, y: self.y, w, h },
            frame,
            text,
        });
        self.right = self.right.max(x + w);
        self.y += h + 40.0 * z;
        self.bottom = self.bottom.max(self.y);
        self.index += 1;
        Some(index)
    }
    fn finish(&self, c: &Canvas) {
        let z = c.zoom.get();
        let x = if self.column.is_some() {
            self.right + 100.0 * z
        } else {
            self.column_x
        };
        c.board.set_size(
            round(x + 400.0 * z) as u32,
            round(self.bottom + 500.0 * z) as u32,
        );
        c.info.set_text(&format!(
            "{}% · Green added / Red deleted / Yellow modified",
            round(100.0 * z)
        ));
        let visible = self.order.len();
        let viewed = self
            .order
            .iter()
            .filter(|(_, i)| c.viewed.borrow().contains(i))
            .count();
        c.count.set_text(&format!("✓ {viewed} / {visible} viewed"));
        c.spec.set_label(&if c.spec.is_active() {
            format!("Specs hidden ({})", c.files.len() - visible)
        } else {
            "Hide specs".into()
        });
        if !self.order.iter().any(|(_, i)| Some(*i) == c.active.get()) {
            c.active.set(self.order.first().map(|(_, i)| *i));
        }
        c.update_location();
    }
}

impl Canvas {
    pub fn cancel_render(&self) {
        if let Some(id) = self.render_source.take() {
            id.remove();
        }
        self.generation.set(self.generation.get().wrapping_add(1));
    }
    fn start_render(&self) -> Layout {
        self.cancel_render();
        self.focused.set(None);
        self.cards.borrow_mut().clear();
        for child in self.board.children() {
            self.board.remove(&child);
        }
        Layout::new(self)
    }
    pub fn render(self: &Rc<Self>) {
        let mut layout = self.start_render();
        while layout.draw_next(self).is_some() {}
        layout.finish(self);
    }
    /// Paint between batches so the modal's progress and Cancel remain responsive.
    pub fn render_incrementally(
        self: &Rc<Self>,
        progress: impl Fn(usize, usize, &str) + 'static,
        done: impl FnOnce() + 'static,
    ) {
        let mut layout = self.start_render();
        let mut done = Some(done);
        let weak = Rc::downgrade(self);
        let id = glib::idle_add_local(move || {
            let Some(c) = weak.upgrade() else {
                return glib::ControlFlow::Break;
            };
            let deadline = Instant::now() + Duration::from_millis(12);
            while let Some(i) = layout.draw_next(&c) {
                progress(layout.index, layout.order.len(), &c.files[i].path);
                if Instant::now() >= deadline {
                    return glib::ControlFlow::Continue;
                }
            }
            c.render_source.take();
            layout.finish(&c);
            if let Some(f) = done.take() {
                f();
            }
            glib::ControlFlow::Break
        });
        self.render_source.replace(Some(id));
    }
    fn put(&self, w: &impl IsA<gtk::Widget>, x: f64, y: f64) -> (f64, f64) {
        self.board.put(w, round(x), round(y));
        w.show_all();
        let natural = w.preferred_size().1;
        (natural.width as f64, natural.height as f64)
    }
    fn connector(
        self: &Rc<Self>,
        styles: &Styles,
        (px, py): (f64, f64),
        x: f64,
        y: f64,
        color: &'static str,
    ) {
        for (left, top, w, h) in [
            (px, py, 2.0, (y - py).max(2.0)),
            (px, y, (x - px).max(2.0), 2.0),
        ] {
            let line = gtk::EventBox::new();
            styles.style(&line, color, color);
            line.set_size_request(round(w), round(h));
            self.put(&line, left, top);
            self.bind_input(&line, None);
        }
    }
    fn make_card(self: &Rc<Self>, i: usize, styles: &Styles) -> (gtk::Box, Option<gtk::TextView>) {
        let file = &self.files[i];
        let (bg, fg) = status_colors(&file.status);
        let z = self.zoom.get();
        let frame = gtk::Box::new(V, 0);
        frame.style_context().add_class("diff-card");
        frame.style_context().add_provider(&styles.border, PRIORITY);
        let header = gtk::Box::new(H, round(6.0 * z).max(1));
        let name = file.path.rsplit('/').next().unwrap_or_default();
        let title = styles.label(&format!("{}  {name}", file.status), bg, fg, z);
        let tooltip = if file.old_path == file.path {
            file.path.clone()
        } else {
            format!("{} → {}", file.old_path, file.path)
        };
        let title_event = gtk::EventBox::new();
        title_event.add(&title);
        title.set_tooltip_text(Some(&tooltip));
        title_event.set_tooltip_text(Some("Click to select this file for reference analysis"));
        header.pack_start(&title_event, true, true, 0);
        let viewed = gtk::CheckButton::with_label("Viewed");
        viewed.set_active(self.viewed.borrow().contains(&i));
        let copy = gtk::Button::with_label("⧉");
        copy.set_tooltip_text(Some("Copy full path"));
        header.pack_start(&viewed, false, false, 0);
        header.pack_start(&copy, false, false, 0);
        styles.style(&header, bg, fg);
        for control in [viewed.upcast_ref::<gtk::Widget>(), copy.upcast_ref()] {
            control.set_valign(gtk::Align::Center);
            control
                .style_context()
                .add_provider(&styles.controls[fg], PRIORITY + 1);
        }
        frame.pack_start(&header, false, false, 0);
        self.bind_input(&title_event, Some(i));
        let path = file.path.clone();
        copy.connect_clicked(move |_| {
            gtk::Clipboard::get(&gdk::SELECTION_CLIPBOARD).set_text(&path)
        });
        let weak = Rc::downgrade(self);
        viewed.connect_toggled(move |b| {
            if let Some(c) = weak.upgrade() {
                let requested = b.is_active();
                if requested != c.viewed.borrow().contains(&i) {
                    c.set_viewed(i, requested);
                    // An unwritable state database must leave both checkboxes
                    // showing the marker that actually persisted.
                    let stored = c.viewed.borrow().contains(&i);
                    if stored != requested {
                        b.set_active(stored);
                    }
                }
            }
        });
        if viewed.is_active() && !self.hits.borrow().iter().any(|hit| hit.file == i) {
            return (frame, None);
        }
        let text = gtk::TextView::new();
        text.set_editable(false);
        text.set_monospace(true);
        text.set_cursor_visible(false);
        text.set_wrap_mode(gtk::WrapMode::WordChar);
        styles.style(&text, "bg", "text_dim");
        let buffer = text.buffer().unwrap();
        let patch = &self.patches[i];
        buffer.set_text(&patch.content);
        let table = buffer.tag_table().unwrap();
        let font_tag = gtk::TextTag::new(Some("font"));
        font_tag.set_font_desc(Some(&styles.font));
        table.add(&font_tag);
        buffer.apply_tag(&font_tag, &buffer.start_iter(), &buffer.end_iter());
        for (tag, bg, fg) in [
            ("add", "add_bg", "add_fg"),
            ("del", "del_bg", "del_fg"),
            ("context", "bg", "text_dim"),
            ("search-hit", "hit_bg", "hit_fg"),
            ("search-current", "current_bg", "current_fg"),
        ] {
            let style = gtk::TextTag::new(Some(tag));
            let background = theme::color(&text, bg);
            let foreground = theme::color(&text, fg);
            if tag.starts_with("search") {
                style.set_background_rgba(Some(&background));
            } else {
                style.set_paragraph_background_rgba(Some(&background));
            }
            style.set_foreground_rgba(Some(&foreground));
            table.add(&style);
        }
        for &(kind, start, end) in &patch.ranges {
            buffer.apply_tag_by_name(
                kind,
                &buffer.iter_at_offset(start),
                &buffer.iter_at_offset(end),
            );
        }
        for h in self.hits.borrow().iter().filter(|hit| hit.file == i) {
            buffer.apply_tag_by_name(
                "search-hit",
                &buffer.iter_at_offset(h.start),
                &buffer.iter_at_offset(h.end),
            );
        }
        let measure = text.create_pango_layout(Some(&patch.content));
        measure.set_font_description(Some(&styles.font));
        let (mut w, mut h) = measure.pixel_size();
        let columns = text.create_pango_layout(Some(&"0".repeat(300)));
        columns.set_font_description(Some(&styles.font));
        let max = columns.pixel_size().0;
        if w > max {
            measure.set_width(max * pango::SCALE);
            measure.set_wrap(pango::WrapMode::WordChar);
            h = measure.pixel_size().1;
            w = max;
        }
        text.set_size_request(round(540.0 * z).max(w + 24), h);
        let weak = Rc::downgrade(self);
        buffer.connect_mark_set(move |buf, _, _| {
            let Some(c) = weak.upgrade() else {
                return;
            };
            if c.updating.get() {
                return;
            }
            if let Some((start, end)) = buf.selection_bounds() {
                let path = c.files[i].path.clone();
                c.clear_button.set_sensitive(true);
                c.clear_button
                    .set_tooltip_text(Some(&format!("Attached to chat: {path}")));
                c.selection.replace(Some(Selection {
                    path,
                    text: buf.text(&start, &end, true).unwrap_or_default().into(),
                    first: start.line() + 1,
                    last: end.line() + i32::from(end.line_offset() != 0),
                }));
            }
        });
        self.bind_input(&text, Some(i));
        frame.pack_start(&text, false, false, 0);
        // GtkTextView validates at first allocation. Ask GTK for its final natural height.
        let first = Cell::new(true);
        frame.connect_size_allocate(move |frame, _| {
            if first.replace(false) {
                let weak = frame.downgrade();
                glib::idle_add_local_once(move || {
                    if let Some(f) = weak.upgrade() {
                        f.queue_resize();
                    }
                });
            }
        });
        (frame, Some(text))
    }
}
