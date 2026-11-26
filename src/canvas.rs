//! A native GTK layout keeps text selectable and lets the scrolled window
//! handle accelerated, two-axis touchpad movement without rebuilding cards.
mod input;
mod model;
mod progress;
mod render;

use gtk::prelude::*;
use gtk::{Orientation::Horizontal as H, Orientation::Vertical as V};
pub use model::{group, is_spec, FilePatch, COLUMNS};
use model::{patch_text, PatchText, Rect};
use progress::Progress;
use std::{
    cell::{Cell, RefCell},
    collections::{HashMap, HashSet},
    rc::Rc,
};

#[derive(Debug, Clone)]
pub struct Selection {
    pub path: String,
    pub text: String,
    pub first: i32,
    pub last: i32,
}

struct Card {
    index: usize,
    rect: Rect,
    frame: gtk::Box,
    text: Option<gtk::TextView>,
}

#[derive(Clone, Copy)]
struct Hit {
    file: usize,
    start: i32,
    end: i32,
}

pub type OpenFile = Rc<dyn Fn(&str)>;

pub struct Canvas {
    pub widget: gtk::Box,
    pub scroll: gtk::ScrolledWindow,
    board: gtk::Layout,
    spec: gtk::ToggleButton,
    info: gtk::Label,
    count: gtk::Label,
    references: gtk::CheckButton,
    path_label: gtk::Label,
    file_label: gtk::Label,
    sticky: gtk::CheckButton,
    open_button: gtk::Button,
    clear_button: gtk::Button,
    search_bar: gtk::Box,
    search_entry: gtk::SearchEntry,
    search_count: gtk::Label,

    files: Vec<FilePatch>,
    patches: Vec<PatchText>,
    natural_sizes: render::NaturalSizes,
    navigation: RefCell<model::Navigation>,
    paths: HashMap<String, usize>,
    progress: Progress,
    viewed: RefCell<HashSet<usize>>,
    open: Option<OpenFile>,
    changed: RefCell<Option<Box<dyn Fn()>>>,
    zoom: Cell<f64>,
    pending_zoom: Cell<Option<f64>>,
    zoom_timer: RefCell<Option<glib::SourceId>>,
    drag: Cell<Option<input::Drag>>,
    pending_pan: Cell<Option<(f64, f64)>>,
    pan_queued: Cell<bool>,
    pinch: RefCell<Option<input::Pinch>>,
    cards: RefCell<Vec<Card>>,
    active: Cell<Option<usize>>,
    focused: Cell<Option<usize>>,
    selection: RefCell<Option<Selection>>,
    updating: Cell<bool>,
    hits: RefCell<Vec<Hit>>,
    hit_index: Cell<Option<usize>>,
    render_source: RefCell<Option<glib::SourceId>>,
    generation: Cell<usize>,
}

impl Canvas {
    pub fn new(
        files: Vec<FilePatch>,
        scope: &str,
        head: &str,
        open: Option<OpenFile>,
    ) -> Result<Rc<Self>, String> {
        let progress = Progress::open(scope, head)?;
        let mut viewed = HashSet::new();
        let mut paths = HashMap::new();
        for (i, f) in files.iter().enumerate() {
            if progress.viewed(f).map_err(|e| e.to_string())? {
                viewed.insert(i);
            }
            paths.insert(f.path.clone(), i);
        }
        let widget = gtk::Box::new(V, 8);
        let tools = gtk::Box::new(H, 8);
        let zoom_out = gtk::Button::with_label("−");
        let zoom_in = gtk::Button::with_label("+");
        let reset = gtk::Button::with_label("100%");
        let fit = gtk::Button::with_label("Fit all");
        fit.set_tooltip_text(Some(
            "Zoom out to see every card, so unchecked files stand out",
        ));
        for b in [&zoom_out, &zoom_in, &reset, &fit] {
            tools.pack_start(b, false, false, 0);
        }
        let spec = gtk::ToggleButton::with_label("Hide specs");
        spec.set_tooltip_text(Some(
            "Hide spec/test files (*.spec.*, *.test.*, test_*.py, spec folders) to reduce noise",
        ));
        tools.pack_start(&spec, false, false, 0);
        let info = gtk::Label::new(None);
        info.set_ellipsize(pango::EllipsizeMode::End);
        info.set_max_width_chars(30);
        tools.pack_start(&info, false, false, 0);
        let count = gtk::Label::new(None);
        tools.pack_start(&count, false, false, 8);
        let hint="Middle-drag to pan · Pinch or Ctrl + wheel to zoom · Double-click a card to center it · Hover filename for full path";
        let help = gtk::Label::new(Some(hint));
        help.set_ellipsize(pango::EllipsizeMode::End);
        help.set_max_width_chars(24);
        help.set_tooltip_text(Some(hint));
        tools.pack_start(&help, true, true, 12);
        widget.pack_start(&tools, false, false, 0);
        let references = gtk::CheckButton::with_label("Reference mode");
        widget.pack_start(&references, false, false, 8);

        let search_bar = gtk::Box::new(H, 8);
        search_bar.set_no_show_all(true);
        let search_entry = gtk::SearchEntry::new();
        search_entry.set_placeholder_text(Some("Search changed text…"));
        search_bar.pack_start(&search_entry, true, true, 0);
        let search_count = gtk::Label::new(Some("0 hits"));
        search_bar.pack_start(&search_count, false, false, 0);
        let prev = gtk::Button::with_label("Previous");
        let next = gtk::Button::with_label("Next");
        let close = gtk::Button::with_label("Close");
        for b in [&prev, &next, &close] {
            search_bar.pack_start(b, false, false, 0);
        }
        widget.pack_start(&search_bar, false, false, 0);

        let location = gtk::Box::new(H, 16);
        location.set_margin(8);
        let labels = gtk::Box::new(V, 4);
        let path_label = gtk::Label::new(Some("Move to a file to see its path"));
        path_label.set_ellipsize(pango::EllipsizeMode::Middle);
        crate::theme::style(&path_label, "muted");
        let file_label = gtk::Label::new(None);
        for label in [&path_label, &file_label] {
            label.set_xalign(0.0);
            label.set_selectable(true);
            labels.pack_start(label, false, false, 0);
        }
        location.pack_start(&labels, true, true, 0);
        let sticky = gtk::CheckButton::with_label("Viewed (V)");
        let open_button = gtk::Button::with_label("Open in Zed");
        let clear_button = gtk::Button::with_label("Clear selection");
        sticky.set_sensitive(false);
        open_button.set_sensitive(false);
        clear_button.set_sensitive(false);
        location.pack_start(&sticky, false, false, 0);
        location.pack_start(&open_button, false, false, 0);
        location.pack_start(&clear_button, false, false, 0);
        widget.pack_start(&location, false, false, 0);

        let scroll = gtk::ScrolledWindow::new(gtk::Adjustment::NONE, gtk::Adjustment::NONE);
        scroll.set_kinetic_scrolling(true);
        let board = gtk::Layout::new(gtk::Adjustment::NONE, gtk::Adjustment::NONE);
        scroll.add(&board);
        let viewport = gtk::Overlay::new();
        viewport.add(&scroll);
        widget.pack_start(&viewport, true, true, 0);
        let patches = files.iter().map(|f| patch_text(&f.patch)).collect();
        let natural_sizes = render::NaturalSizes::new(files.len());
        let canvas = Rc::new(Self {
            widget,
            scroll,
            board,
            spec,
            info,
            count,
            references,
            path_label,
            file_label,
            sticky,
            open_button,
            clear_button,
            search_bar,
            search_entry,
            search_count,
            files,
            patches,
            natural_sizes,
            navigation: RefCell::default(),
            paths,
            progress,
            viewed: RefCell::new(viewed),
            open,
            changed: RefCell::default(),
            zoom: Cell::new(1.0),
            pending_zoom: Cell::default(),
            zoom_timer: RefCell::default(),
            drag: Cell::default(),
            pending_pan: Cell::default(),
            pan_queued: Cell::new(false),
            pinch: RefCell::default(),
            cards: RefCell::default(),
            active: Cell::default(),
            focused: Cell::default(),
            selection: RefCell::default(),
            updating: Cell::new(false),
            hits: RefCell::default(),
            hit_index: Cell::default(),
            render_source: RefCell::default(),
            generation: Cell::default(),
        });
        for (button, action) in [(&zoom_in, 1.2), (&zoom_out, 1.0 / 1.2), (&reset, 0.0)] {
            let weak = Rc::downgrade(&canvas);
            button.connect_clicked(move |_| {
                if let Some(c) = weak.upgrade() {
                    c.set_zoom(if action == 0.0 {
                        1.0
                    } else {
                        c.zoom.get() * action
                    })
                }
            });
        }
        canvas
            .references
            .connect_toggled(glib::clone!(@weak canvas => move |_| {
                canvas.update_reference_output();
            }));
        fit.connect_clicked(glib::clone!(@weak canvas => move |_| canvas.fit_all()));
        canvas
            .spec
            .connect_toggled(glib::clone!(@weak canvas => move |_| canvas.search_changed()));
        close.connect_clicked(glib::clone!(@weak canvas => move |_| canvas.close_search()));
        prev.connect_clicked(glib::clone!(@weak canvas => move |_| canvas.search_next(-1)));
        next.connect_clicked(glib::clone!(@weak canvas => move |_| canvas.search_next(1)));
        canvas
            .search_entry
            .connect_search_changed(glib::clone!(@weak canvas => move |_| canvas.search_changed()));
        canvas.search_entry.connect_key_press_event(glib::clone!(@weak canvas => @default-return glib::Propagation::Proceed, move |_,e| {
            use gdk::keys::constants as key;
            match e.keyval() {
                key::Escape => canvas.close_search(),
                key::Return | key::KP_Enter => canvas.search_next(if e.state().contains(gdk::ModifierType::SHIFT_MASK) {-1} else {1}),
                _ => return glib::Propagation::Proceed,
            }
            glib::Propagation::Stop
        }));
        canvas.sticky.connect_toggled(glib::clone!(@weak canvas => move |b| {
            if !canvas.updating.get() { if let Some(i)=canvas.active.get(){canvas.set_viewed(i,b.is_active());} }
        }));
        canvas
            .open_button
            .connect_clicked(glib::clone!(@weak canvas => move |_| {
                if let (Some(file),Some(open))=(canvas.active_file(),&canvas.open) {
                    if !file.status.starts_with('D'){open(&file.path);}
                }
            }));
        canvas
            .clear_button
            .connect_clicked(glib::clone!(@weak canvas => move |_| canvas.clear_selection()));
        canvas.bind_input(&canvas.board, None);
        canvas.pinch.replace(Some(input::pinch(&canvas)));
        Ok(canvas)
    }

    pub fn files(&self) -> &[FilePatch] {
        &self.files
    }
    pub fn active_file(&self) -> Option<&FilePatch> {
        self.active.get().map(|i| &self.files[i])
    }
    pub fn selection(&self) -> Option<Selection> {
        self.selection.borrow().clone()
    }
    pub fn is_viewed(&self, path: &str) -> bool {
        self.paths
            .get(path)
            .is_some_and(|i| self.viewed.borrow().contains(i))
    }
    pub fn on_viewed_changed(&self, callback: impl Fn() + 'static) {
        self.changed.replace(Some(Box::new(callback)));
    }
    pub fn toggle_viewed(self: &Rc<Self>) {
        if let Some(i) = self.active.get() {
            let v = !self.viewed.borrow().contains(&i);
            self.set_viewed(i, v);
        }
    }
    fn set_viewed(self: &Rc<Self>, i: usize, value: bool) {
        if let Err(e) = self.progress.set(&self.files[i], value) {
            self.count
                .set_text(&format!("Could not save viewed marker: {e}"));
            eprintln!("Could not save viewed marker: {e}");
            self.update_location();
            return;
        }
        if value {
            self.viewed.borrow_mut().insert(i);
        } else {
            self.viewed.borrow_mut().remove(&i);
        }
        self.render();
        self.jump_to_file(&self.files[i].path);
        if let Some(callback) = &*self.changed.borrow() {
            callback();
        }
    }
    fn update_reference_output(&self) {
        // The review builder deliberately does not resolve or infer dependency
        // edges. Preserve the original per-file summary when the mode is used.
        let summary = self.active_file().filter(|_| self.references.is_active()).map(|file| {
            format!("Reference links for {}\nResolved from source in this PR checkout. Lines show where each link occurs.\n\nNo direct links to or from other changed files.", file.path)
        });
        self.references.set_tooltip_text(summary.as_deref());
    }

    fn update_location(&self) {
        let active = self.active.get();
        let previous = self.focused.replace(active);
        if previous != active {
            self.update_reference_output();
            for c in self.cards.borrow().iter() {
                if Some(c.index) == previous {
                    c.frame.style_context().remove_class("focused-card");
                }
                if Some(c.index) == active {
                    crate::theme::style(&c.frame, "focused-card");
                }
            }
        }
        self.updating.set(true);
        if let Some(i) = active {
            let file = &self.files[i];
            let (folder, name) = file.path.rsplit_once('/').unwrap_or(("", &file.path));
            self.path_label.set_text(&if folder.is_empty() {
                "(repository root)".into()
            } else {
                folder.replace('/', " › ")
            });
            self.path_label.set_tooltip_text(Some(&file.path));
            self.file_label.set_text(name);
            self.sticky.set_active(self.viewed.borrow().contains(&i));
            self.open_button
                .set_sensitive(self.open.is_some() && !file.status.starts_with('D'));
        } else {
            self.path_label.set_text("No file selected");
            self.path_label.set_tooltip_text(None);
            self.file_label.set_text("");
            self.sticky.set_active(false);
            self.open_button.set_sensitive(false);
        }
        self.sticky.set_sensitive(active.is_some());
        self.updating.set(false);
    }
    fn clear_selection(&self) {
        self.updating.set(true);
        for c in self.cards.borrow().iter() {
            if let Some(buffer) = c.text.as_ref().and_then(|t| t.buffer()) {
                buffer.place_cursor(&buffer.start_iter());
            }
        }
        self.selection.take();
        self.clear_button.set_sensitive(false);
        self.clear_button.set_tooltip_text(None);
        self.updating.set(false);
    }
    pub fn open_search(&self) {
        self.search_bar.set_no_show_all(false);
        self.search_bar.show_all();
        self.search_entry.grab_focus();
    }
    fn close_search(&self) {
        self.search_entry.set_text("");
        self.search_bar.hide();
        self.search_bar.set_no_show_all(true);
    }
    fn search_changed(self: &Rc<Self>) {
        let query = self.search_entry.text();
        let mut hits = Vec::new();
        if !query.is_empty() {
            let regex = regex::RegexBuilder::new(&regex::escape(&query))
                .case_insensitive(true)
                .build()
                .expect("escaped query");
            for (i, f) in self.files.iter().enumerate() {
                if self.spec.is_active() && is_spec(&f.path) {
                    continue;
                }
                let content = &self.patches[i].content;
                let (mut pos, mut chars) = (0, 0);
                for m in regex.find_iter(content) {
                    let start = chars + content[pos..m.start()].chars().count() as i32;
                    chars = start + m.as_str().chars().count() as i32;
                    pos = m.end();
                    hits.push(Hit {
                        file: i,
                        start,
                        end: chars,
                    });
                }
            }
        }
        self.search_count.set_text(&format!("{} hits", hits.len()));
        self.hits.replace(hits);
        self.hit_index.set(None);
        self.render();
        self.search_next(1);
    }
    fn search_next(self: &Rc<Self>, step: i32) {
        let n = self.hits.borrow().len();
        if n == 0 {
            return;
        }
        let index =
            (self.hit_index.get().map_or(-1, |v| v as i32) + step).rem_euclid(n as i32) as usize;
        if let Some(old) = self.hit_index.replace(Some(index)) {
            let old = self.hits.borrow()[old];
            self.highlight_hit(old, false);
        }
        let hit = self.hits.borrow()[index];
        self.search_count.set_text(&format!("{} / {n}", index + 1));
        self.jump_to_file(&self.files[hit.file].path);
        self.after_layout(move |c| c.highlight_hit(hit, true));
    }
    fn highlight_hit(&self, hit: Hit, current: bool) {
        let cards = self.cards.borrow();
        let Some(card) = cards.iter().find(|c| c.index == hit.file) else {
            return;
        };
        let Some(text) = &card.text else {
            return;
        };
        let buffer = text.buffer().unwrap();
        let start = buffer.iter_at_offset(hit.start);
        let end = buffer.iter_at_offset(hit.end);
        if current {
            buffer.apply_tag_by_name("search-current", &start, &end);
            let top = text.allocation().y();
            self.scroll
                .vadjustment()
                .set_value(card.rect.y + (top + text.iter_location(&start).y()) as f64);
        } else {
            buffer.remove_tag_by_name("search-current", &start, &end);
        }
    }
    fn after_layout(self: &Rc<Self>, f: impl FnOnce(&Canvas) + 'static) {
        let weak = Rc::downgrade(self);
        let gen = self.generation.get();
        glib::idle_add_local_once(move || {
            if let Some(c) = weak.upgrade() {
                if c.generation.get() == gen {
                    f(&c);
                }
            }
        });
    }
}

impl Drop for Canvas {
    fn drop(&mut self) {
        if let Some(id) = self.zoom_timer.get_mut().take() {
            id.remove();
        }
        if let Some(id) = self.render_source.get_mut().take() {
            id.remove();
        }
    }
}
