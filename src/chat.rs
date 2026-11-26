//! GTK sidebar panels. GitHub and Pi run on workers, and only the GTK thread
//! touches widgets or changes which review is active.
mod conversations;
mod notes;

use crate::backend::{self, pi, pr_scope, str_field, Result};
use crate::widgets::{button, clear, entry, hbox, label, scroll, vbox};
use gtk::prelude::*;
use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::Duration;

pub struct Sidebar {
    pub chat: gtk::Box,
    pub notes: gtk::Box,
    pub conversations: gtk::Box,
    assistant: Rc<Chat>,
    notebook: Rc<notes::Notes>,
    threads: Rc<conversations::Conversations>,
}

impl Sidebar {
    pub fn new(
        root: PathBuf,
        context: impl Fn() -> Result<Value> + 'static,
        busy: impl Fn(bool) + 'static,
    ) -> Self {
        let assistant = Chat::new(root.clone(), context, busy);
        let notebook = notes::Notes::new();
        let threads = conversations::Conversations::new(root);
        Self {
            chat: assistant.panel.clone(),
            notes: notebook.panel.clone(),
            conversations: threads.panel.clone(),
            assistant,
            notebook,
            threads,
        }
    }
    pub fn configure(&self, number: Option<u64>) -> Result<()> {
        if self.assistant.number.get() == number {
            return Ok(());
        }
        if self.assistant.busy.get() {
            return Err("Stop the chat before switching PRs.".into());
        }
        let history = number
            .map(|n| pi::history(&pr_scope(n)))
            .transpose()?
            .unwrap_or_default();
        self.notebook.configure(number)?;
        self.assistant.configure(number, history);
        self.threads.configure(number);
        Ok(())
    }
    pub fn load_conversations(&self) {
        self.threads.load();
    }
    pub fn stop(&self) {
        self.assistant.cancel.store(true, Ordering::Relaxed);
        if let Some(worker) = self.assistant.worker.borrow_mut().take() {
            if worker.join().is_err() {
                eprintln!("Pi worker stopped unexpectedly.");
            }
        }
        self.threads.stop();
        if let Err(e) = self.notebook.flush() {
            eprintln!("{e}");
        }
    }
}

struct Card {
    widget: gtk::Box,
    content: gtk::Box,
    runtime: Option<gtk::Label>,
}

impl Card {
    fn new(list: &gtk::Box, user: bool, anchor: &str, text: &str) -> Self {
        let widget = vbox(10);
        widget.set_margin(2);
        widget
            .style_context()
            .add_class(if user { "chat-user" } else { "chat-agent" });
        let role = if user { "You" } else { "Agent" };
        let heading = label(
            &if anchor.is_empty() {
                role.to_owned()
            } else {
                format!("{role} · {anchor}")
            },
            &["chat-role"],
        );
        heading.set_ellipsize(pango::EllipsizeMode::Middle);
        widget.pack_start(&heading, false, false, 0);
        let content = vbox(10);
        widget.pack_start(&content, false, false, 0);
        list.pack_start(&widget, false, false, 0);
        render_message(&content, text);
        widget.show_all();
        Self {
            widget,
            content,
            runtime: None,
        }
    }

    fn runtime(&mut self, info: &Value) {
        let meta = self.runtime.get_or_insert_with(|| {
            let meta = label("", &["muted"]);
            meta.set_selectable(true);
            meta.set_ellipsize(pango::EllipsizeMode::Middle);
            self.widget.pack_start(&meta, false, false, 0);
            meta.show();
            meta
        });
        let text = runtime_text(info);
        meta.set_text(&text);
        meta.set_tooltip_text(Some(&text));
    }
}

fn runtime_text(info: &Value) -> String {
    let model = ["provider", "model"]
        .iter()
        .filter_map(|key| info[key].as_str().filter(|s| !s.is_empty()))
        .collect::<Vec<_>>()
        .join("/");
    let source = if info["source"] == "target" {
        "Target"
    } else {
        "Used"
    };
    format!(
        "{source}: {} · Effort (Pi setting): {}",
        if model.is_empty() { "unknown" } else { &model },
        info["effort"].as_str().unwrap_or("unknown")
    )
}

fn copy_text(text: &str) {
    gtk::Clipboard::get(&gdk::SELECTION_CLIPBOARD).set_text(text);
}

fn render_message(body: &gtk::Box, text: &str) {
    clear(body);
    let mut language = None;
    let mut chunk = String::new();
    let mut blocks = Vec::new();
    for line in text.split_inclusive('\n') {
        if let Some(suffix) = line.trim_start().strip_prefix("```") {
            if !chunk.is_empty() {
                blocks.push((language.clone(), std::mem::take(&mut chunk)));
            }
            language = if language.is_none() {
                Some(suffix.trim().to_owned())
            } else {
                None
            };
        } else {
            chunk.push_str(line);
        }
    }
    if !chunk.is_empty() {
        blocks.push((language, chunk));
    }
    for (language, text) in blocks {
        if let Some(language) = language {
            let panel = vbox(6);
            panel.style_context().add_class("chat-code");
            let bar = hbox(8);
            bar.pack_start(
                &label(
                    if language.is_empty() {
                        "Code"
                    } else {
                        &language
                    },
                    &[],
                ),
                true,
                true,
                0,
            );
            let copy = button("Copy");
            let value = text.clone();
            copy.connect_clicked(move |_| copy_text(&value));
            bar.pack_end(&copy, false, false, 0);
            panel.pack_start(&bar, false, false, 0);
            let code = label(text.trim_end_matches('\n'), &["code-text"]);
            code.set_selectable(true);
            panel.pack_start(&code, false, false, 0);
            let wrap = scroll(&panel);
            wrap.set_policy(gtk::PolicyType::Automatic, gtk::PolicyType::Never);
            body.pack_start(&wrap, false, false, 0);
        } else {
            let para = label(text.trim(), &[]);
            para.set_selectable(true);
            para.set_line_wrap(true);
            para.set_line_wrap_mode(pango::WrapMode::WordChar);
            para.set_max_width_chars(44);
            body.pack_start(&para, false, false, 0);
        }
    }
    body.show_all();
}

struct Chat {
    panel: gtk::Box,
    model: gtk::Label,
    context_label: gtk::Label,
    messages: gtk::Box,
    scroll: gtk::ScrolledWindow,
    input: gtk::Entry,
    send: gtk::Button,
    stop: gtk::Button,
    status: gtk::Label,
    root: PathBuf,
    number: Cell<Option<u64>>,
    busy: Cell<bool>,
    cancel: Arc<AtomicBool>,
    worker: RefCell<Option<std::thread::JoinHandle<()>>>,
    context: Box<dyn Fn() -> backend::Result<Value>>,
    busy_changed: Box<dyn Fn(bool)>,
    history: RefCell<Vec<Value>>,
    reply: RefCell<Option<Card>>,
}
impl Chat {
    fn new(
        root: PathBuf,
        context: impl Fn() -> Result<Value> + 'static,
        busy: impl Fn(bool) + 'static,
    ) -> Rc<Self> {
        let panel = vbox(10);
        panel.set_margin(12);
        panel.set_size_request(390, -1);
        let heading = hbox(8);
        heading.pack_start(&label("Ask Pi · Read-only", &[]), true, true, 0);
        let copy = button("Copy chat");
        copy.set_tooltip_text(Some("Copy this chat as plain text"));
        heading.pack_end(&copy, false, false, 0);
        panel.pack_start(&heading, false, false, 0);
        let model = label("Model / effort: checked when sending", &["muted"]);
        model.set_ellipsize(pango::EllipsizeMode::Middle);
        panel.pack_start(&model, false, false, 0);
        let context_label = label("Select a PR, then open its diff map.", &[]);
        context_label.set_line_wrap(true);
        context_label.set_line_wrap_mode(pango::WrapMode::WordChar);
        context_label.set_max_width_chars(42);
        context_label.set_lines(3);
        context_label.set_ellipsize(pango::EllipsizeMode::End);
        context_label.set_yalign(0.);
        fn size(l: &gtk::Label) {
            l.set_size_request(-1, l.create_pango_layout(Some("Ag\nAg\nAg")).pixel_size().1);
        }
        size(&context_label);
        context_label.connect_style_updated(size);
        panel.pack_start(&context_label, false, false, 0);
        let messages = vbox(14);
        messages.set_margin(8);
        let view = scroll(&messages);
        view.set_policy(gtk::PolicyType::Never, gtk::PolicyType::Automatic);
        panel.pack_start(&view, true, true, 0);
        let input = entry("Ask about this file…");
        panel.pack_start(&input, false, false, 0);
        let actions = hbox(8);
        let send = button("Send");
        let stop = button("Stop");
        send.set_sensitive(false);
        stop.set_sensitive(false);
        actions.pack_start(&send, false, false, 0);
        actions.pack_start(&stop, false, false, 0);
        panel.pack_start(&actions, false, false, 0);
        let status = label("", &[]);
        status.set_line_wrap(true);
        status.set_line_wrap_mode(pango::WrapMode::WordChar);
        status.set_max_width_chars(42);
        panel.pack_start(&status, false, false, 0);
        let obj = Rc::new(Self {
            panel,
            model,
            context_label,
            messages,
            scroll: view,
            input,
            send,
            stop,
            status,
            root,
            number: Cell::new(None),
            busy: Cell::new(false),
            cancel: Arc::new(AtomicBool::new(false)),
            worker: Default::default(),
            context: Box::new(context),
            busy_changed: Box::new(busy),
            history: Default::default(),
            reply: Default::default(),
        });
        let weak = Rc::downgrade(&obj);
        obj.send.connect_clicked(move |_| {
            if let Some(c) = weak.upgrade() {
                c.send();
            }
        });
        let weak = Rc::downgrade(&obj);
        obj.input.connect_activate(move |_| {
            if let Some(c) = weak.upgrade() {
                c.send();
            }
        });
        let weak = Rc::downgrade(&obj);
        obj.stop.connect_clicked(move |_| {
            if let Some(c) = weak.upgrade() {
                c.cancel.store(true, Ordering::Relaxed);
            }
        });
        let weak = Rc::downgrade(&obj);
        copy.connect_clicked(move |_| {
            if let Some(c) = weak.upgrade() {
                c.copy();
            }
        });
        let weak = Rc::downgrade(&obj);
        glib::timeout_add_local(Duration::from_millis(400), move || {
            let Some(c) = weak.upgrade() else {
                return glib::ControlFlow::Break;
            };
            if !c.busy.get() {
                c.preview();
            }
            glib::ControlFlow::Continue
        });
        obj
    }

    fn configure(&self, number: Option<u64>, history: Vec<Value>) {
        self.number.set(number);
        self.reply.replace(None);
        self.model.set_text("Model / effort: checked when sending");
        clear(&self.messages);
        for item in &history {
            let anchor = str_field(&item["context"], "head")
                .chars()
                .take(10)
                .collect::<String>();
            let user = item["role"] == "user";
            let mut card = Card::new(&self.messages, user, &anchor, str_field(item, "text"));
            if !user {
                card.runtime(&item["runtime"]);
            }
        }
        self.history.replace(history);
        self.set_busy(false);
    }
    fn copy(&self) {
        let Some(number) = self.number.get() else {
            self.status.set_text("Select a PR to copy its chat.");
            return;
        };
        let mut lines = vec!["Code Flow chat".into(), pr_scope(number), String::new()];
        for row in self.history.borrow().iter() {
            let role = if row["role"] == "user" {
                "You"
            } else {
                "Agent"
            };
            let head = str_field(&row["context"], "head")
                .chars()
                .take(10)
                .collect::<String>();
            lines.push(if head.is_empty() {
                role.into()
            } else {
                format!("{role} · {head}")
            });
            lines.push(str_field(row, "text").into());
            lines.push(String::new());
        }
        copy_text(&(lines.join("\n").trim_end().to_owned() + "\n"));
        self.status.set_text("Chat copied to clipboard.");
    }
    fn preview(&self) {
        let message = match (self.context)() {
            Ok(value) => {
                let mut s = format!(
                    "PR #{} · {}\n{}",
                    value["pr"],
                    str_field(&value, "head")
                        .chars()
                        .take(10)
                        .collect::<String>(),
                    value["file"].as_str().unwrap_or("PR overview")
                );
                if let Some(rows) = value["selection_diff_rows"]
                    .as_array()
                    .filter(|r| r.len() == 2)
                {
                    s.push_str(&format!(
                        "\nAttached: diff rows {}–{} (until cleared)",
                        rows[0], rows[1]
                    ));
                }
                s
            }
            Err(e) => e,
        };
        self.context_label.set_text(&message.replace('\n', " · "));
        self.context_label.set_tooltip_text(Some(&message));
    }
    fn set_busy(&self, busy: bool) {
        self.busy.set(busy);
        self.input.set_sensitive(!busy);
        self.send
            .set_sensitive(self.number.get().is_some() && !busy);
        self.stop.set_sensitive(busy);
        (self.busy_changed)(busy);
    }
    fn send(self: &Rc<Self>) {
        let Some(number) = self.number.get().filter(|_| !self.busy.get()) else {
            return;
        };
        let question = self.input.text().trim().to_owned();
        if question.is_empty() {
            return;
        }
        let context = match (self.context)() {
            Ok(c) => c,
            Err(e) => {
                self.status.set_text(&e);
                return;
            }
        };
        self.preview();
        self.input.set_text("");
        self.set_busy(true);
        self.model.set_text("Checking model and effort…");
        self.status.set_text("Connecting to Pi…");
        self.reply.replace(None);
        self.cancel.store(false, Ordering::Relaxed);
        let (tx, rx) = std::sync::mpsc::channel();
        let root = self.root.clone();
        let cancel = self.cancel.clone();
        let request = context.clone();
        let q = question.clone();
        let worker = std::thread::spawn(move || {
            pi::ask(&pr_scope(number), &root, &request, &q, &cancel, |e| {
                let _ = tx.send(e);
            })
        });
        self.worker.replace(Some(worker));
        let weak = Rc::downgrade(self);
        let mut answer = String::new();
        glib::timeout_add_local(Duration::from_millis(24), move || {
            let Some(obj) = weak.upgrade() else {
                return glib::ControlFlow::Break;
            };
            let mut redraw = false;
            let mut finished = false;
            for event in rx.try_iter() {
                match event {
                    pi::Event::User => {
                        let head = str_field(&context, "head")
                            .chars()
                            .take(10)
                            .collect::<String>();
                        let path = context["file"].as_str().unwrap_or("overview");
                        Card::new(&obj.messages, true, &format!("{head} · {path}"), &question);
                        obj.history
                            .borrow_mut()
                            .push(json!({"role":"user","text":question,"context":context}));
                        obj.reply
                            .replace(Some(Card::new(&obj.messages, false, "", "")));
                        obj.history
                            .borrow_mut()
                            .push(json!({"role":"assistant","text":"","context":context}));
                    }
                    pi::Event::Runtime(v) => {
                        let msg = runtime_text(&v);
                        obj.model.set_text(&msg);
                        obj.model.set_tooltip_text(Some(&msg));
                        if let Some(card) = obj.reply.borrow_mut().as_mut() {
                            card.runtime(&v);
                        }
                        if let Some(row) = obj.history.borrow_mut().last_mut() {
                            row["runtime"] = v;
                        }
                    }
                    pi::Event::Delta(s) => {
                        answer.push_str(&s);
                        redraw = true;
                    }
                    pi::Event::Status(s) => obj.status.set_text(&s),
                    pi::Event::Finished(result) => {
                        match result {
                            Ok(()) => {
                                obj.status.set_text("Ready");
                                redraw = true;
                                if answer.is_empty() {
                                    answer.push_str("(No text response.)");
                                }
                            }
                            Err(e) => {
                                eprintln!("Pi chat: {e}");
                                obj.status.set_text(&e);
                                if obj.reply.borrow().is_some() {
                                    if !answer.is_empty() {
                                        answer.push_str("\n\n");
                                    }
                                    answer.push_str(&format!("[Incomplete] {e}"));
                                    redraw = true;
                                }
                            }
                        }
                        obj.set_busy(false);
                        finished = true;
                    }
                }
            }
            if redraw {
                let adjustment = obj.scroll.vadjustment();
                let was_at_bottom =
                    adjustment.value() + adjustment.page_size() >= adjustment.upper() - 40.;
                if let Some(card) = obj.reply.borrow().as_ref() {
                    if finished {
                        // A finished live reply includes its final paragraph break.
                        // History retains the model's original response text.
                        render_message(&card.content, &format!("{answer}\n\n"));
                    } else {
                        render_message(&card.content, &answer);
                    }
                }
                if let Some(row) = obj.history.borrow_mut().last_mut() {
                    row["text"] = json!(answer);
                }
                if was_at_bottom {
                    glib::idle_add_local_once(move || {
                        adjustment.set_value((adjustment.upper() - adjustment.page_size()).max(0.))
                    });
                }
            }
            if finished {
                glib::ControlFlow::Break
            } else {
                glib::ControlFlow::Continue
            }
        });
    }
}
