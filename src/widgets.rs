use gtk::prelude::*;

pub fn vbox(spacing: i32) -> gtk::Box {
    gtk::Box::new(gtk::Orientation::Vertical, spacing)
}
pub fn hbox(spacing: i32) -> gtk::Box {
    gtk::Box::new(gtk::Orientation::Horizontal, spacing)
}
pub fn label(text: &str, classes: &[&str]) -> gtk::Label {
    let label = gtk::Label::new(Some(text));
    label.set_xalign(0.);
    for class in classes {
        label.style_context().add_class(class);
    }
    label
}
pub fn button(text: &str) -> gtk::Button {
    gtk::Button::with_label(text)
}
pub fn entry(placeholder: &str) -> gtk::Entry {
    let entry = gtk::Entry::new();
    entry.set_placeholder_text(Some(placeholder));
    entry
}
pub fn scroll(widget: &impl IsA<gtk::Widget>) -> gtk::ScrolledWindow {
    let scroll = gtk::ScrolledWindow::new(gtk::Adjustment::NONE, gtk::Adjustment::NONE);
    scroll.add(widget);
    scroll
}
pub fn clear(container: &impl IsA<gtk::Container>) {
    for widget in container.children() {
        container.remove(&widget);
    }
}

pub struct Progress {
    pub widget: gtk::Box,
    title: gtk::Label,
    bar: gtk::ProgressBar,
    steps: Vec<gtk::Label>,
    spinner: gtk::Spinner,
    detail: gtk::Label,
    pub button: gtk::Button,
    stage: std::cell::Cell<usize>,
}

const STAGES: [&str; 3] = ["Checkout", "Read changes", "Draw map"];

impl Progress {
    pub fn new() -> Self {
        let widget = vbox(16);
        widget.set_margin(32);
        widget.set_hexpand(true);
        widget.set_valign(gtk::Align::Center);
        let title = label("Building diff map", &["review-title"]);
        widget.pack_start(&title, false, false, 0);
        let bar = gtk::ProgressBar::new();
        bar.set_show_text(true);
        bar.set_tooltip_text(Some(
            "Progress through the build steps, not an estimate of remaining time.",
        ));
        widget.pack_start(&bar, false, false, 0);
        let steps = STAGES
            .iter()
            .map(|stage| {
                let step = label(stage, &[]);
                widget.pack_start(&step, false, false, 0);
                step
            })
            .collect();
        let row = hbox(8);
        let spinner = gtk::Spinner::new();
        row.pack_start(&spinner, false, false, 0);
        let detail = label("", &[]);
        detail.set_selectable(true);
        detail.set_ellipsize(pango::EllipsizeMode::Middle);
        detail.set_max_width_chars(90);
        row.pack_start(&detail, true, true, 0);
        widget.pack_start(&row, false, false, 0);
        let button = button("Cancel build");
        button.set_halign(gtk::Align::Start);
        widget.pack_start(&button, false, false, 0);
        Self {
            widget,
            title,
            bar,
            steps,
            spinner,
            detail,
            button,
            stage: 0.into(),
        }
    }
    pub fn start(&self) {
        self.title.set_text("Building diff map");
        self.button.set_label("Cancel build");
        self.button.set_sensitive(true);
        self.spinner.start();
        self.update(0, "Preparing the review clone…", 0.);
    }
    pub fn update(&self, stage: usize, detail: &str, fraction: f64) {
        self.stage.set(stage);
        for (i, step) in self.steps.iter().enumerate() {
            let marker = match i.cmp(&stage) {
                std::cmp::Ordering::Less => "✓",
                std::cmp::Ordering::Equal => "→",
                std::cmp::Ordering::Greater => "○",
            };
            step.set_text(&format!("{marker} {}. {}", i + 1, STAGES[i]));
        }
        self.bar
            .set_fraction((stage as f64 + fraction.clamp(0., 1.)) / 3.);
        self.bar.set_text(Some(&format!(
            "Step {} of 3 · {}",
            stage + 1,
            STAGES[stage]
        )));
        self.detail.set_text(detail);
        self.detail.set_tooltip_text(Some(detail));
    }
    pub fn cancelling(&self) {
        self.button.set_sensitive(false);
        self.detail
            .set_text("Cancelling… Waiting for the current process to stop.");
    }
    pub fn failed(&self, message: &str) {
        let i = self.stage.get();
        self.spinner.stop();
        self.title
            .set_text(&format!("Build stopped · {}", STAGES[i]));
        self.steps[i].set_text(&format!("× {}. {}", i + 1, STAGES[i]));
        self.detail.set_text(message);
        self.detail.set_tooltip_text(Some(message));
        self.button.set_label("Back to overview");
        self.button.set_sensitive(true);
    }
    pub fn finish(&self) {
        self.spinner.stop();
        self.bar.set_fraction(1.);
        self.bar.set_text(Some("Diff map ready"));
    }
}

pub fn help(parent: &gtk::Window) -> gtk::Window {
    const SECTIONS: &[(&str, &[(&str, &str)])] = &[
        (
            "Move around the diff map",
            &[
                (
                    "Click a card",
                    "Select it. Scrolling never changes the selection",
                ),
                (
                    "↑ ↓ ← →",
                    "Select the nearest file card in that direction and center it",
                ),
                ("Shift + ↑ ↓ ← →", "Scroll the map in that direction"),
                ("V", "Mark the selected file as viewed"),
                ("Ctrl + F", "Search inside the diffs"),
            ],
        ),
        (
            "Zoom and pan",
            &[
                ("Ctrl + scroll, + / −", "Zoom in and out"),
                ("Two-finger scroll, middle-drag", "Pan the map"),
                ("Double-click a card", "Zoom in and center that card"),
                (
                    "Fit all",
                    "Zoom out to see every card and what is still unviewed",
                ),
            ],
        ),
        (
            "Reduce noise",
            &[
                ("Hide specs", "Remove test and spec files from the map"),
                (
                    "Reference mode",
                    "Show which changed files the selected file uses, and which use it",
                ),
            ],
        ),
        (
            "Anywhere",
            &[
                (
                    "Ctrl + B, Sidebar",
                    "Hide or show the sidebar (chat, files, notes) for more room",
                ),
                ("?", "Show this help again"),
            ],
        ),
    ];
    let window = gtk::Window::new(gtk::WindowType::Toplevel);
    window.set_title("Code Flow — Shortcuts");
    window.set_default_size(620, -1);
    window.set_transient_for(Some(parent));
    window.set_destroy_with_parent(true);
    let body = vbox(6);
    body.set_margin(24);
    body.pack_start(&label("KEYS AND FEATURES", &["eyebrow"]), false, false, 0);
    for (title, rows) in SECTIONS {
        body.pack_start(
            &label(&title.to_uppercase(), &["section-title"]),
            false,
            false,
            0,
        );
        let grid = gtk::Grid::new();
        grid.set_column_spacing(16);
        grid.set_row_spacing(6);
        for (i, (keys, info)) in rows.iter().enumerate() {
            let keycap = label(keys, &["keycap"]);
            keycap.set_valign(gtk::Align::Center);
            keycap.set_halign(gtk::Align::Start);
            let text = label(info, &[]);
            text.set_line_wrap(true);
            grid.attach(&keycap, 0, i as i32, 1, 1);
            grid.attach(&text, 1, i as i32, 1, 1);
        }
        body.pack_start(&grid, false, false, 0);
    }
    let close = button("Got it");
    close.style_context().add_class("primary");
    close.set_halign(gtk::Align::End);
    close.set_margin_top(12);
    close.connect_clicked(glib::clone!(@weak window => move |_| window.close()));
    body.pack_start(&close, false, false, 0);
    window.add(&body);
    window.show_all();
    window
}
