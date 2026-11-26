use crate::{backend, canvas::Canvas, chat::Sidebar, widgets::*};
use gdk::{keys::constants as key, ModifierType};
use glib::{clone, ControlFlow, Propagation};
use gtk::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    cell::{Cell, OnceCell, RefCell},
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
    rc::Rc,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc,
    },
    thread,
    time::Duration,
};

#[derive(Clone, Serialize, Deserialize)]
struct Recent {
    mode: String,
    value: String,
    title: String,
}

pub struct App {
    window: gtk::Window,
    root: PathBuf,
    state_dir: PathBuf,
    bar: gtk::Box,
    mode: gtk::ComboBoxText,
    selector: gtk::Stack,
    branch: gtk::Entry,
    pr: gtk::Entry,
    base: gtk::Entry,
    options: RefCell<Vec<String>>,
    menu: gtk::Popover,
    choices: gtk::ListBox,
    home: gtk::Button,
    status: gtk::Label,
    actions: gtk::Box,
    diff: gtk::Button,
    github: gtk::LinkButton,
    views: gtk::Stack,
    overview: gtk::Box,
    progress: Progress,
    notebook: gtk::Notebook,
    file_list: gtk::Box,
    sidebar: OnceCell<Sidebar>,
    help: RefCell<Option<gtk::Window>>,
    selection: RefCell<Option<(String, Option<Value>)>>,
    head: RefCell<String>,
    board: RefCell<Option<Rc<Canvas>>>,
    cancel: RefCell<Option<Arc<AtomicBool>>>,
    working: Cell<bool>,
    chatting: Cell<bool>,
    closed: Cell<bool>,
}

impl App {
    pub fn new() -> Rc<Self> {
        let root = backend::review_root();
        let state_dir = backend::state_dir();
        let window = gtk::Window::new(gtk::WindowType::Toplevel);
        window.set_title("Code Flow — Review");
        window.set_widget_name("code-flow-review");
        window.set_default_size(1400, 900);
        let icon = gdk_pixbuf::PixbufLoader::new();
        if icon
            .write(include_bytes!("../code-flow.svg"))
            .and_then(|_| icon.close())
            .is_ok()
        {
            window.set_icon(icon.pixbuf().as_ref());
        }
        let outer = vbox(8);
        outer.set_margin(10);
        window.add(&outer);
        let bar = hbox(8);
        outer.pack_start(&bar, false, false, 0);
        let branch = entry("Search branches…");
        branch
            .set_icon_from_icon_name(gtk::EntryIconPosition::Secondary, Some("pan-down-symbolic"));
        let menu = gtk::Popover::new(Some(&branch));
        menu.set_modal(false);
        menu.set_position(gtk::PositionType::Bottom);
        let choices = gtk::ListBox::new();
        let options_scroll = scroll(&choices);
        options_scroll.set_size_request(520, 300);
        menu.add(&options_scroll);
        let base = entry("Base branch");
        let pr = entry("PR number — press Enter");
        let mode = gtk::ComboBoxText::new();
        mode.append_text("Branch");
        mode.append_text("Pull request");
        mode.set_active(Some(0));
        let selector = gtk::Stack::new();
        selector.add_named(&branch, "branch");
        selector.add_named(&pr, "pr");
        bar.pack_start(&mode, false, false, 0);
        bar.pack_start(&selector, true, true, 0);
        let home = button("⌂");
        home.set_tooltip_text(Some("Back to recent reviews"));
        let refresh = button("↻");
        let toggle = button("Sidebar");
        let shortcuts = button("?");
        for item in [&home, &refresh, &toggle, &shortcuts] {
            bar.pack_start(item, false, false, 0);
        }
        let status = label("Loading branches…", &["muted"]);
        status.set_selectable(true);
        outer.pack_start(&status, false, false, 0);
        let actions = hbox(12);
        let diff = button("Open diff map");
        crate::theme::style(&diff, "primary");
        let github =
            gtk::LinkButton::with_label("https://github.com/plancraft/plancraft", "View on GitHub");
        actions.pack_start(&diff, false, false, 0);
        actions.pack_start(&github, false, false, 0);
        actions.set_no_show_all(true);
        outer.pack_start(&actions, false, false, 0);
        let overview = hbox(24);
        let views = gtk::Stack::new();
        views.add_named(&scroll(&overview), "overview");
        let progress = Progress::new();
        views.add_named(&progress.widget, "loading");
        let notebook = gtk::Notebook::new();
        crate::theme::style(&notebook, "review-sidebar");
        let split = gtk::Paned::new(gtk::Orientation::Horizontal);
        split.pack1(&views, true, false);
        split.pack2(&notebook, false, false);
        split.set_position(950);
        outer.pack_start(&split, true, true, 0);
        let app = Rc::new(Self {
            window,
            root,
            state_dir,
            bar,
            mode,
            selector,
            branch,
            pr,
            base,
            options: Default::default(),
            menu,
            choices,
            home,
            status,
            actions,
            diff,
            github,
            views,
            overview,
            progress,
            notebook,
            file_list: vbox(4),
            sidebar: OnceCell::new(),
            help: Default::default(),
            selection: Default::default(),
            head: Default::default(),
            board: Default::default(),
            cancel: Default::default(),
            working: false.into(),
            chatting: false.into(),
            closed: false.into(),
        });
        let weak = Rc::downgrade(&app);
        let weak_busy = weak.clone();
        let sidebar = Sidebar::new(
            app.root.clone(),
            move || {
                weak.upgrade()
                    .ok_or_else(|| "Review closed.".to_string())?
                    .context()
            },
            move |busy| {
                if let Some(app) = weak_busy.upgrade() {
                    app.chatting.set(busy);
                    app.sync_busy();
                }
            },
        );
        let files = scroll(&app.file_list);
        files.set_size_request(320, -1);
        for (page, title) in [
            (sidebar.chat.upcast_ref::<gtk::Widget>(), "Chat"),
            (files.upcast_ref(), "Files"),
            (sidebar.conversations.upcast_ref(), "Conversations"),
            (sidebar.notes.upcast_ref(), "Notes"),
        ] {
            app.notebook
                .append_page(page, Some(&gtk::Label::new(Some(title))));
        }
        let _ = app.sidebar.set(sidebar);
        refresh.connect_clicked(clone!(@weak app => move |_| app.refresh(true)));
        toggle.connect_clicked(clone!(@weak app => move |_| app.toggle_sidebar()));
        shortcuts.connect_clicked(clone!(@weak app => move |_| app.show_help()));
        app.connect();
        app
    }

    fn side(&self) -> &Sidebar {
        self.sidebar
            .get()
            .expect("sidebar initialized before signals")
    }

    fn connect(self: &Rc<Self>) {
        let app = self;
        self.window.connect_destroy(clone!(@weak app => move |_| {
            app.closed.set(true);
            if let Some(cancel) = app.cancel.borrow().as_ref() { cancel.store(true, Ordering::Release); }
            if let Some(canvas) = app.board.borrow().as_ref() { canvas.cancel_render(); }
            app.side().stop();
            gtk::main_quit();
        }));
        self.mode.connect_changed(clone!(@weak app => move |mode| {
            app.selector.set_visible_child_name(if mode.active() == Some(0) {"branch"} else {"pr"});
            app.menu.popdown();
        }));
        self.branch
            .connect_changed(clone!(@weak app => move |_| app.filter_branches()));
        self.branch
            .connect_icon_press(clone!(@weak app => move |_,_,_| {
                if app.menu.is_visible() { app.menu.popdown(); } else { app.filter_branches(); }
            }));
        self.branch.connect_key_press_event(clone!(@weak app => @default-return Propagation::Proceed, move |_, event| {
            match event.keyval() {
                key::Escape => {app.menu.popdown(); Propagation::Stop}
                key::Down => {
                    app.filter_branches();
                    if let Some(row) = app.choices.row_at_index(0).filter(|r| r.is_activatable()) { row.grab_focus(); }
                    Propagation::Stop
                },
                _ => Propagation::Proceed,
            }
        }));
        self.choices
            .connect_row_activated(clone!(@weak app => move |_,row| {
                if !row.is_activatable() { return; }
                if let Some(label) = row.child().and_then(|w| w.downcast::<gtk::Label>().ok()) {
                    app.branch.set_text(&label.text());
                    app.menu.popdown();
                    app.branch.grab_focus();
                    app.branch.set_position(-1);
                    app.load();
                }
            }));
        self.pr
            .connect_activate(clone!(@weak app => move |_| app.load()));
        self.branch
            .connect_activate(clone!(@weak app => move |_| app.load()));
        self.home
            .connect_clicked(clone!(@weak app => move |_| app.reset()));
        self.diff.connect_clicked(clone!(@weak app => move |_| {
            if app.map_visible() { app.show_overview(); } else { app.build(); }
        }));
        self.progress.button.connect_clicked(clone!(@weak app => move |_| {
            if app.cancel.borrow().is_some() { app.cancel_build(); } else { app.show_overview(); }
        }));
        self.notebook
            .connect_switch_page(clone!(@weak app => move |_,_,n| {
                if n == 2 { app.side().load_conversations(); }
            }));
        self.window.connect_key_press_event(
            clone!(@weak app => @default-return Propagation::Proceed, move |_,event| {
                app.shortcut(event).into()
            }),
        );
    }

    pub fn start(self: &Rc<Self>) {
        if let Err(e) = fs::create_dir_all(&self.state_dir) {
            self.error(e.to_string());
        }
        self.recent_panel();
        self.refresh(false);
        self.window.show_all();
        if self.state_dir.join("sidebar-hidden").exists() {
            self.notebook.hide();
        }
        if !self.state_dir.join("help-seen").exists() {
            let app = self;
            glib::idle_add_local_once(clone!(@weak app => move || app.show_help()));
        }
        eprintln!("Review clone: {}", self.root.display());
    }

    fn error(&self, error: String) {
        eprintln!("Code Flow error: {error}");
        self.status.set_text(&error);
    }

    fn show_help(self: &Rc<Self>) {
        if let Some(window) = self.help.borrow().as_ref() {
            window.present();
            return;
        }
        let window = help(&self.window);
        let app = self;
        window.connect_destroy(clone!(@weak app => move |_| { app.help.take(); }));
        self.help.replace(Some(window));
        if let Err(e) = fs::write(self.state_dir.join("help-seen"), []) {
            eprintln!("Remember help: {e}");
        }
    }

    fn toggle_sidebar(&self) {
        let hidden = self.notebook.is_visible();
        self.notebook.set_visible(!hidden);
        let path = self.state_dir.join("sidebar-hidden");
        if let Err(e) = if hidden {
            fs::write(path, [])
        } else {
            fs::remove_file(path)
        } {
            if e.kind() != io::ErrorKind::NotFound {
                self.error(e.to_string());
            }
        }
    }

    fn shortcut(self: &Rc<Self>, event: &gdk::EventKey) -> bool {
        let mods = event.state();
        let command =
            ModifierType::CONTROL_MASK | ModifierType::META_MASK | ModifierType::SUPER_MASK;
        let focus = self.window.focused_widget();
        let typing = focus.as_ref().is_some_and(|w| {
            w.is::<gtk::Entry>()
                || w.downcast_ref::<gtk::TextView>()
                    .is_some_and(|t| t.is_editable())
        });
        let keyval = event.keyval();
        // Let the notes editor consume its selection formatting accelerator.
        if matches!(keyval, key::b | key::B) && mods.intersects(command) {
            if typing
                && self.notebook.current_page() == Some(3)
                && focus.as_ref().is_some_and(|w| w.is::<gtk::TextView>())
            {
                return false;
            }
            self.toggle_sidebar();
            return true;
        }
        if keyval == key::question && !typing {
            self.show_help();
            return true;
        }
        if !self.map_visible() {
            return false;
        }
        let board = self.board.borrow();
        let Some(board) = board.as_ref() else {
            return false;
        };
        if matches!(keyval, key::f | key::F) && mods.intersects(command) {
            board.open_search();
            return true;
        }
        if typing || mods.intersects(command | ModifierType::MOD1_MASK) {
            return false;
        }
        let dir = match keyval {
            key::Up => "up",
            key::Down => "down",
            key::Left => "left",
            key::Right => "right",
            key::v | key::V if board.active_file().is_some() => {
                board.toggle_viewed();
                return true;
            }
            _ => return false,
        };
        if mods.contains(ModifierType::SHIFT_MASK) {
            board.scroll_by(dir);
            true
        } else {
            board.navigate(dir)
        }
    }

    fn map_visible(&self) -> bool {
        self.views.visible_child_name().as_deref() == Some("map")
    }

    fn sync_busy(&self) {
        let busy = self.working.get() || self.chatting.get() || self.cancel.borrow().is_some();
        self.bar.set_sensitive(!busy);
        self.actions.set_sensitive(!busy);
        if let Some(side) = self.sidebar.get() {
            side.chat
                .set_sensitive(!self.working.get() && self.cancel.borrow().is_none());
        }
    }

    fn task<T: Send + 'static>(
        self: &Rc<Self>,
        lock_selection: bool,
        work: impl FnOnce() -> backend::Result<T> + Send + 'static,
        done: impl FnOnce(&Rc<Self>, T) + 'static,
    ) {
        if lock_selection {
            self.working.set(true);
            self.sync_busy();
        }
        let weak = Rc::downgrade(self);
        let work = gio::spawn_blocking(work);
        glib::MainContext::default().spawn_local(async move {
            let result = work
                .await
                .unwrap_or_else(|_| Err("Background task stopped unexpectedly.".into()));
            let Some(app) = weak.upgrade().filter(|app| !app.closed.get()) else {
                return;
            };
            if lock_selection {
                app.working.set(false);
                app.sync_busy();
            }
            match result {
                Ok(value) => done(&app, value),
                Err(e) => app.error(e),
            }
        });
    }

    fn refresh(self: &Rc<Self>, fetch: bool) {
        let root = self.root.clone();
        self.task(
            true,
            move || backend::branches(&root, fetch),
            |app, (branches, base)| {
                app.options.replace(branches);
                app.base.set_text(&base);
                app.status
                    .set_text("Select a branch to load its PR overview.");
            },
        );
    }

    fn filter_branches(&self) {
        clear(&self.choices);
        let options = self.options.borrow();
        let matches = fuzzy(&self.branch.text(), &options);
        if matches.is_empty() {
            let row = gtk::ListBoxRow::new();
            row.set_activatable(false);
            row.set_selectable(false);
            let label = gtk::Label::new(Some("No matching branches"));
            label.set_margin(6);
            row.add(&label);
            self.choices.add(&row);
        }
        for name in matches {
            let label = label(name, &[]);
            label.set_margin(6);
            self.choices.add(&label);
        }
        self.menu.show_all();
        self.menu.popup();
    }

    fn load(self: &Rc<Self>) {
        if self.working.get() || self.chatting.get() || self.cancel.borrow().is_some() {
            return;
        }
        self.menu.popdown();
        if let Err(e) = self.side().configure(None) {
            self.error(e);
            return;
        }
        let branch = self.branch.text().trim().to_owned();
        let number = (self.mode.active() == Some(1)).then(|| self.pr.text().trim().to_owned());
        self.discard_map();
        self.selection.take();
        clear(&self.overview);
        self.refresh_files();
        self.actions.hide();
        self.status.set_text("Loading PR overview…");
        let root = self.root.clone();
        let name = branch.clone();
        self.task(
            true,
            move || backend::overview(&root, &name, number.as_deref()),
            move |app, metadata| {
                app.selection.replace(Some((branch, metadata)));
                app.show_overview();
            },
        );
    }

    fn discard_map(&self) {
        self.views.set_visible_child_name("overview");
        self.head.take();
        if let Some(board) = self.board.take() {
            board.cancel_render();
            self.views.remove(&board.widget);
        }
    }

    fn reset(self: &Rc<Self>) {
        if let Err(e) = self.side().configure(None) {
            self.error(e);
            return;
        }
        self.discard_map();
        self.selection.take();
        self.actions.hide();
        self.home.hide();
        self.branch.set_text("");
        self.pr.set_text("");
        self.base.set_text("");
        self.menu.popdown();
        self.notebook.set_current_page(Some(0));
        self.recent_panel();
        self.refresh_files();
        self.status
            .set_text("Select a branch or PR to load its overview.");
    }

    fn recent_panel(self: &Rc<Self>) {
        clear(&self.overview);
        let panel = vbox(8);
        panel.set_margin(24);
        panel.set_halign(gtk::Align::Center);
        panel.set_valign(gtk::Align::Start);
        self.overview.pack_start(&panel, true, true, 0);
        let items = read_recent(&self.state_dir).unwrap_or_else(|e| {
            self.error(format!("Could not read recent selections: {e}"));
            Vec::new()
        });
        if !items.is_empty() {
            panel.pack_start(&label("Recent reviews", &[]), false, false, 0);
            for item in items {
                let is_pr = item.mode == "pr";
                let button = button(&format!(
                    "{} · {}",
                    if is_pr { "Pr" } else { "Branch" },
                    item.title
                ));
                let app = self;
                button.connect_clicked(clone!(@weak app => move |_| {
                    app.mode.set_active(Some(if is_pr {1} else {0}));
                    (if is_pr {&app.pr} else {&app.branch}).set_text(&item.value);
                    app.load();
                }));
                panel.pack_start(&button, false, false, 0);
            }
        }
        panel.show_all();
    }

    fn remember(&self, entry: Recent) {
        if let Err(e) = remember_recent(&self.state_dir, entry) {
            self.error(format!("Could not save recent reviews: {e}"));
        }
    }

    fn show_overview(self: &Rc<Self>) {
        let selection = self.selection.borrow();
        let Some((branch, metadata)) = selection.as_ref() else {
            return;
        };
        let number = metadata.as_ref().and_then(|p| p["number"].as_u64());
        if let Err(e) = self.side().configure(number) {
            self.error(e);
            return;
        }
        if self.notebook.current_page() == Some(2) {
            self.side().load_conversations();
        }
        self.views.set_visible_child_name("overview");
        self.diff.set_label("Open diff map");
        self.home.show();
        clear(&self.overview);
        self.refresh_files();
        let panel = vbox(14);
        panel.set_margin(16);
        panel.set_size_request(700, -1);
        panel.set_valign(gtk::Align::Start);
        crate::theme::style(&panel, "overview-card");
        self.overview.pack_start(&panel, true, true, 0);
        let add = |text: &str, classes: &[&str]| {
            let widget = label(text, classes);
            widget.set_selectable(true);
            widget.set_line_wrap(true);
            widget.set_max_width_chars(85);
            if classes.contains(&"badge") {
                widget.set_halign(gtk::Align::Start);
            }
            panel.pack_start(&widget, false, false, 0);
        };
        if let Some(pr) = metadata {
            let field = |name: &str| pr[name].as_str().unwrap_or_default();
            let num = number.unwrap_or_default();
            self.remember(Recent {
                mode: "pr".into(),
                value: num.to_string(),
                title: field("title").into(),
            });
            add(&format!("PULL REQUEST  #{num}"), &["eyebrow"]);
            add(field("title"), &["review-title"]);
            let state = match field("state") {
                "OPEN" if pr["isDraft"] == true => "Draft",
                "OPEN" => "Open",
                "CLOSED" => "Closed",
                "MERGED" => "Merged",
                s => s,
            };
            let decision = match field("reviewDecision") {
                "APPROVED" => "Approved",
                "CHANGES_REQUESTED" => "Changes requested",
                "REVIEW_REQUIRED" => "Review required",
                _ => "No review decision",
            };
            add(
                &format!("{state} · {decision}"),
                &[
                    "badge",
                    if decision == "Changes requested" {
                        "attention"
                    } else {
                        "neutral"
                    },
                ],
            );
            add(
                &format!("{}  →  {}", field("headRefName"), field("baseRefName")),
                &["muted"],
            );
            let checks = backend::checks_summary(&pr["statusCheckRollup"]);
            add(
                &checks,
                &[
                    "badge",
                    if checks == "Checks: no failures" {
                        "good"
                    } else {
                        "neutral"
                    },
                ],
            );
            if let Some(count) = pr["changedFiles"].as_u64() {
                let stats = hbox(24);
                crate::theme::style(&stats, "stats");
                for (text, class) in [
                    (format!("{count} changed files"), "stat-files"),
                    (
                        format!("+{} added", grouped(pr["additions"].as_u64().unwrap_or(0))),
                        "stat-added",
                    ),
                    (
                        format!(
                            "−{} removed",
                            grouped(pr["deletions"].as_u64().unwrap_or(0))
                        ),
                        "stat-removed",
                    ),
                ] {
                    let widget = label(&text, &[class]);
                    widget.set_selectable(true);
                    stats.pack_start(&widget, false, false, 0);
                }
                panel.pack_start(&stats, false, false, 0);
            }
            add("PURPOSE", &["section-title"]);
            add(&backend::purpose_excerpt(field("body")), &["purpose"]);
            add(
                "From the PR description · Full details and discussion on GitHub",
                &["muted"],
            );
            self.github.set_uri(field("url"));
        } else {
            self.remember(Recent {
                mode: "branch".into(),
                value: branch.clone(),
                title: branch.clone(),
            });
            add(branch, &[]);
            add("No pull request found for this branch.", &[]);
            add("Compare with", &[]);
            if let Some(parent) = self
                .base
                .parent()
                .and_then(|w| w.downcast::<gtk::Container>().ok())
            {
                parent.remove(&self.base);
            }
            panel.pack_start(&self.base, false, false, 0);
        }
        self.status
            .set_text("Overview · Details and discussion on GitHub.");
        self.actions.show();
        self.diff.show();
        self.github.set_visible(metadata.is_some());
        self.overview.show_all();
    }

    fn refresh_files(&self) {
        clear(&self.file_list);
        let board = self.board.borrow();
        if let Some(board) = board.as_ref().filter(|_| self.map_visible()) {
            let mut files = board.files().iter().collect::<Vec<_>>();
            files.sort_by(|a, b| a.path.cmp(&b.path));
            for file in files {
                let path = file.path.clone();
                let name = label(
                    &format!("{} {path}", if board.is_viewed(&path) { "✓" } else { "○" }),
                    &[],
                );
                name.set_ellipsize(pango::EllipsizeMode::Middle);
                let btn = gtk::Button::new();
                crate::theme::style(&btn, "file-row");
                btn.set_tooltip_text(Some(&path));
                btn.add(&name);
                btn.connect_clicked(clone!(@weak board => move |_| board.jump_to_file(&path)));
                self.file_list.pack_start(&btn, false, false, 0);
            }
        } else {
            self.file_list.pack_start(
                &gtk::Label::new(Some("Open the diff map to browse files.")),
                false,
                false,
                8,
            );
        }
        self.file_list.show_all();
    }

    fn build(self: &Rc<Self>) {
        if self.cancel.borrow().is_some() || self.chatting.get() {
            return;
        }
        let Some((branch, pr)) = self.selection.borrow().clone() else {
            return;
        };
        self.discard_map();
        let cancel = Arc::new(AtomicBool::new(false));
        self.cancel.replace(Some(cancel.clone()));
        self.sync_busy();
        self.progress.start();
        self.views.set_visible_child_name("loading");
        self.status.set_text("Building diff map…");
        let root = self.root.clone();
        let base = self.base.text().trim().to_owned();
        enum Event {
            Step(usize, String, f64),
            Done(backend::Result<backend::Review>),
        }
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            let result = backend::build(&root, &branch, &base, pr.as_ref(), &cancel, |i, s, f| {
                let _ = tx.send(Event::Step(i, s.to_owned(), f));
            });
            let _ = tx.send(Event::Done(result));
        });
        let app = self;
        glib::timeout_add_local(
            Duration::from_millis(16),
            clone!(@weak app => @default-return ControlFlow::Break, move || {
                if app.closed.get() { return ControlFlow::Break; }
                let mut pending = None;
                loop {
                    match rx.try_recv() {
                        Ok(Event::Step(i,s,f)) => pending = Some((i,s,f)),
                        Ok(Event::Done(res)) => {
                            if let Some((i,s,f)) = pending { app.progress.update(i,&s,f); }
                            if app.cancelled() { app.build_failed("Review build cancelled.".into()); }
                            else { match res { Ok(review) => app.draw_map(review), Err(e) => app.build_failed(e) } }
                            return ControlFlow::Break;
                        }
                        Err(mpsc::TryRecvError::Empty) => break,
                        Err(_) => { app.build_failed("Build worker stopped unexpectedly.".into()); return ControlFlow::Break; }
                    }
                }
                if !app.cancelled() { if let Some((i,s,f)) = pending { app.progress.update(i,&s,f); } }
                ControlFlow::Continue
            }),
        );
    }

    fn cancelled(&self) -> bool {
        self.cancel
            .borrow()
            .as_ref()
            .is_none_or(|c| c.load(Ordering::Acquire))
    }
    fn cancel_build(&self) {
        if let Some(cancel) = self.cancel.borrow().as_ref() {
            cancel.store(true, Ordering::Release);
        }
        self.progress.cancelling();
        if let Some(board) = self.board.borrow().clone() {
            board.cancel_render();
        } else {
            return;
        }
        self.build_failed("Build cancelled.".into());
    }
    fn build_failed(&self, message: String) {
        self.cancel.take();
        if let Some(board) = self.board.take() {
            board.cancel_render();
            self.views.remove(&board.widget);
        }
        self.progress.failed(&message);
        self.error(format!("Diff map not built: {message}"));
        self.sync_busy();
    }
    fn draw_map(self: &Rc<Self>, review: backend::Review) {
        let app = self;
        let open = Rc::new(clone!(@weak app => move |path: &str| {
            let root = app.root.clone();
            let path = path.to_owned();
            app.task(false, move || backend::open_in_zed(&root,&path,None,None), |_,_| {});
        }));
        let count = review.files.len();
        let board = match Canvas::new(review.files, &review.scope, &review.head, Some(open)) {
            Ok(board) => board,
            Err(e) => {
                self.build_failed(e);
                return;
            }
        };
        self.views.add_named(&board.widget, "map");
        board.widget.show_all();
        self.board.replace(Some(board.clone()));
        board.on_viewed_changed(clone!(@weak app => move || app.refresh_files()));
        board.render_incrementally(
            clone!(@weak app => move |count,total,path| {
                if !app.cancelled() {
                    app.progress.update(2, &format!("{count} / {total} file cards · {path}"), count as f64 / total.max(1) as f64);
                }
            }),
            clone!(@weak app => move || {
                if app.cancelled() || app.closed.get() { return; }
                app.head.replace(review.head.clone());
                app.views.set_visible_child_name("map");
                app.refresh_files();
                app.diff.set_label("Back to overview");
                let msg = format!("{count} files · {} → {} · Diff map", short(&review.merge), short(&review.head));
                eprintln!("{msg}");
                app.status.set_text(&msg);
                app.progress.finish();
                app.cancel.take();
                app.sync_busy();
            }),
        );
    }

    fn context(&self) -> backend::Result<Value> {
        let selected = self.selection.borrow();
        let pr = selected
            .as_ref()
            .and_then(|(_, pr)| pr.as_ref())
            .ok_or("Select a PR to open its own chat.")?;
        let head = self.head.borrow();
        if head.is_empty() || pr["headRefOid"].as_str() != Some(&head) {
            return Err("Open the diff map first so Pi can read the reviewed code.".into());
        }
        let board = self.board.borrow();
        let selection = board.as_ref().and_then(|b| b.selection());
        let file = board.as_ref().and_then(|b| match &selection {
            Some(s) => b.files().iter().find(|f| f.path == s.path),
            None if self.map_visible() => b.active_file(),
            None => None,
        });
        let selected = selection.as_ref().map_or("", |s| s.text.as_str());
        let (text, truncated) = bounded(selected, 12000);
        let (diff, diff_truncated) = bounded(file.map_or("", |f| &f.patch), 50000);
        Ok(json!({
            "pr":pr["number"],"url":pr["url"],"title":pr["title"],"head":head.as_str(),
            "description":bounded(pr["body"].as_str().unwrap_or(""),12000).0,
            "file":file.map(|f| &f.path),"diff":file.map(|_| diff),"diff_truncated":diff_truncated,
            "selection":text,"selection_truncated":truncated,
            "selection_diff_rows":selection.as_ref().map(|s| [s.first,s.last]),
        }))
    }
}

fn bounded(text: &str, limit: usize) -> (&str, bool) {
    match text.char_indices().nth(limit) {
        Some((end, _)) => (&text[..end], true),
        None => (text, false),
    }
}
fn short(sha: &str) -> &str {
    bounded(sha, 10).0
}
fn grouped(count: u64) -> String {
    let digits = count.to_string();
    let mut output = String::new();
    for (i, c) in digits.char_indices() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            output.push(',');
        }
        output.push(c);
    }
    output
}
fn read_recent(dir: &Path) -> io::Result<Vec<Recent>> {
    match fs::read(dir.join("recent-selections.json")) {
        Ok(raw) => Ok(serde_json::from_slice(&raw)?),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(vec![]),
        Err(e) => Err(e),
    }
}
fn remember_recent(dir: &Path, entry: Recent) -> io::Result<()> {
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(dir.join("recent-selections.lock"))?;
    fs2::FileExt::lock_exclusive(&lock)?;
    let mut items = read_recent(dir)?;
    items.retain(|r| r.mode != entry.mode || r.value != entry.value);
    items.insert(0, entry);
    items.truncate(8);
    let mut pending = tempfile::NamedTempFile::new_in(dir)?;
    serde_json::to_writer_pretty(&mut pending, &items)?;
    pending.flush()?;
    pending.persist(dir.join("recent-selections.json"))?;
    Ok(())
}
fn fuzzy<'a>(query: &str, options: &'a [String]) -> Vec<&'a str> {
    let query: String = query
        .to_lowercase()
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect();
    let mut list = Vec::new();
    for (i, name) in options.iter().enumerate() {
        let folded = name.to_lowercase();
        let mut chars = folded.chars().enumerate();
        let mut first = None;
        let mut last = 0;
        if query.chars().all(|q| {
            if let Some((pos, _)) = chars.find(|(_, c)| *c == q) {
                first.get_or_insert(pos);
                last = pos;
                true
            } else {
                false
            }
        }) {
            list.push((
                !folded.contains(&query),
                last - first.unwrap_or(0),
                i,
                name.as_str(),
            ));
        }
    }
    list.sort_unstable();
    list.into_iter().map(|(_, _, _, name)| name).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn branch_ranking_prefers_contiguous_then_input_order() {
        let options = [
            "feat/einvoice-error-checklist-model",
            "feat/einvcheck",
            "other",
        ]
        .map(String::from);
        assert_eq!(
            fuzzy("EINV CHECK", &options),
            [options[1].as_str(), options[0].as_str()]
        );
        assert_eq!(fuzzy("", &options), options);
        assert!(fuzzy("absent", &options).is_empty());
    }
    #[test]
    fn simultaneous_recent_updates_merge_and_bad_state_is_preserved() {
        let folder = tempfile::tempdir().unwrap();
        let dir = folder.path();
        std::thread::scope(|scope| {
            for n in 1..=8 {
                scope.spawn(move || {
                    remember_recent(
                        dir,
                        Recent {
                            mode: "pr".into(),
                            value: n.to_string(),
                            title: n.to_string(),
                        },
                    )
                    .unwrap()
                });
            }
        });
        let mut values = read_recent(dir)
            .unwrap()
            .into_iter()
            .map(|r| r.value)
            .collect::<Vec<_>>();
        values.sort();
        assert_eq!(values, ["1", "2", "3", "4", "5", "6", "7", "8"]);
        let state = dir.join("recent-selections.json");
        fs::write(&state, b"{invalid").unwrap();
        assert!(remember_recent(
            dir,
            Recent {
                mode: "pr".into(),
                value: "9".into(),
                title: "ninth".into()
            }
        )
        .is_err());
        assert_eq!(fs::read(state).unwrap(), b"{invalid");
    }
    #[test]
    fn boundaries_count_unicode_characters() {
        assert_eq!(bounded("aé🙂z", 3), ("aé🙂", true));
        assert_eq!(bounded("aé🙂", 3), ("aé🙂", false));
    }
}
