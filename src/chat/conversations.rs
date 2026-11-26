use crate::backend::{discussions, str_field};
use crate::widgets::{button, clear, hbox, label, scroll, vbox};
use gtk::prelude::*;
use std::cell::{Cell, RefCell};
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::Duration;

pub struct Conversations {
    pub panel: gtk::Box,
    root: PathBuf,
    rows: gtk::Box,
    message: gtk::Label,
    number: Cell<Option<u64>>,
    generation: Cell<u64>,
    items: RefCell<Vec<discussions::Thread>>,
    loaded: Cell<bool>,
    active: RefCell<Option<(Arc<AtomicBool>, std::thread::JoinHandle<()>)>>,
}

impl Conversations {
    pub fn new(root: PathBuf) -> Rc<Self> {
        let panel = vbox(8);
        panel.set_margin(8);
        let refresh = button("Refresh conversations");
        panel.pack_start(&refresh, false, false, 0);
        let message = label("Select a PR to see conversations.", &[]);
        message.set_line_wrap(true);
        panel.pack_start(&message, false, false, 0);
        let rows = vbox(8);
        let view = scroll(&rows);
        view.set_policy(gtk::PolicyType::Never, gtk::PolicyType::Automatic);
        panel.pack_start(&view, true, true, 0);
        let obj = Rc::new(Self {
            panel,
            root,
            rows,
            message,
            number: Cell::new(None),
            generation: Cell::new(0),
            items: Default::default(),
            loaded: Cell::new(false),
            active: Default::default(),
        });
        let weak = Rc::downgrade(&obj);
        refresh.connect_clicked(move |_| {
            if let Some(obj) = weak.upgrade() {
                obj.loaded.set(false);
                obj.load();
            }
        });
        obj
    }

    pub fn stop(&self) {
        self.generation.set(self.generation.get() + 1);
        if let Some((cancel, worker)) = self.active.borrow_mut().take() {
            cancel.store(true, Ordering::Relaxed);
            if worker.join().is_err() {
                eprintln!("Conversation loader stopped unexpectedly.");
            }
        }
    }

    pub fn configure(&self, n: Option<u64>) {
        if self.number.replace(n) == n {
            return;
        }
        self.stop();
        self.loaded.set(false);
        self.items.borrow_mut().clear();
        clear(&self.rows);
        self.message.set_text(if n.is_some() {
            "Loading conversations…"
        } else {
            "Select a PR to see conversations."
        });
    }

    pub fn load(self: &Rc<Self>) {
        let Some(number) = self.number.get() else {
            return;
        };
        if self.loaded.replace(true) {
            return;
        }
        self.stop();
        let generation = self.generation.get();
        self.message.set_text("Loading conversations…");
        let root = self.root.clone();
        let (send, recv) = std::sync::mpsc::channel();
        let cancel = Arc::new(AtomicBool::new(false));
        let request_cancel = cancel.clone();
        let worker = std::thread::spawn(move || {
            let _ = send.send(discussions::load(&root, number, &request_cancel));
        });
        self.active.replace(Some((cancel, worker)));
        let weak = Rc::downgrade(self);
        glib::timeout_add_local(Duration::from_millis(30), move || {
            let Some(obj) = weak.upgrade().filter(|o| o.generation.get() == generation) else {
                return glib::ControlFlow::Break;
            };
            match recv.try_recv() {
                Ok(Ok(items)) => {
                    obj.message.set_text(&format!(
                        "{} conversations · Hiding only affects this tool.",
                        items.len()
                    ));
                    obj.items.replace(items);
                    obj.render();
                    glib::ControlFlow::Break
                }
                Ok(Err(error)) => {
                    eprintln!("Could not load conversations: {error}");
                    obj.message
                        .set_text(&format!("Could not load conversations: {error}"));
                    glib::ControlFlow::Break
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(_) => {
                    obj.message.set_text("Conversation loader stopped.");
                    glib::ControlFlow::Break
                }
            }
        });
    }

    fn row(self: &Rc<Self>, thread: &discussions::Thread, hidden: bool) -> gtk::Box {
        let row = hbox(4);
        let author = thread
            .comments
            .first()
            .map(|c| c["author"]["login"].as_str().unwrap_or("Deleted user"))
            .unwrap_or("Unknown");
        let title = format!(
            "{author} · {} · {} comments",
            thread.title,
            thread.comments.len()
        );
        let expand = gtk::Expander::new(None);
        let heading = label(&title, &[]);
        heading.set_ellipsize(pango::EllipsizeMode::Middle);
        heading.set_tooltip_text(Some(&title));
        expand.set_label_widget(Some(&heading));
        let body = vbox(12);
        body.set_margin(8);
        for c in &thread.comments {
            let author = c["author"]["login"].as_str().unwrap_or("Deleted user");
            let text = label(
                &format!(
                    "{author} · {}\n\n{}",
                    str_field(c, "createdAt"),
                    str_field(c, "body")
                ),
                &[],
            );
            text.set_selectable(true);
            text.set_line_wrap(true);
            text.set_line_wrap_mode(pango::WrapMode::WordChar);
            text.set_max_width_chars(45);
            body.pack_start(&text, false, false, 0);
            if let Some(url) = c["url"].as_str().filter(|s| s.starts_with("https://")) {
                body.pack_start(
                    &gtk::LinkButton::with_label(url, "View on GitHub"),
                    false,
                    false,
                    0,
                );
            }
        }
        expand.add(&body);
        row.pack_start(&expand, true, true, 0);
        let toggle = button(if hidden { "Unhide" } else { "Hide" });
        toggle.set_valign(gtk::Align::Start);
        let weak = Rc::downgrade(self);
        let id = thread.id.clone();
        toggle.connect_clicked(move |_| {
            if let Some(obj) = weak.upgrade() {
                if let Some(n) = obj.number.get() {
                    match discussions::set_hidden(n, &id, !hidden) {
                        Ok(()) => obj.render(),
                        Err(e) => obj
                            .message
                            .set_text(&format!("Could not save hidden state: {e}")),
                    }
                }
            }
        });
        row.pack_start(&toggle, false, false, 0);
        row
    }
    fn render(self: &Rc<Self>) {
        clear(&self.rows);
        let Some(n) = self.number.get() else { return };
        let hidden = match discussions::hidden(n) {
            Ok(hidden) => hidden,
            Err(e) => {
                self.message.set_text(&e);
                return;
            }
        };
        let hidden_rows = vbox(8);
        let mut count = 0;
        for item in self.items.borrow().iter() {
            let hide = hidden.contains(&item.id);
            if hide {
                hidden_rows.pack_start(&self.row(item, true), false, false, 0);
                count += 1;
            } else {
                self.rows
                    .pack_start(&self.row(item, false), false, false, 0);
            }
        }
        if count > 0 {
            let group = gtk::Expander::new(Some(&format!("{count} conversations hidden")));
            group.add(&hidden_rows);
            self.rows.pack_start(&group, false, false, 0);
        }
        self.rows.show_all();
    }
}
