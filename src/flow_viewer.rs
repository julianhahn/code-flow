use crate::{
    flow_model::{self, Flow, Positions},
    widgets::*,
};
use anyhow::{anyhow, ensure, Context, Result};
use glib::{clone, Propagation};
use gtk::prelude::*;
use rusqlite::{Connection, OpenFlags};
use serde_json::Value;
use std::{
    cell::{Cell, RefCell},
    path::{Path, PathBuf},
    process::Command,
    rc::Rc,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

const W: f64 = 370.;
const H: f64 = 180.;
const EXAMPLE: &[u8] = include_bytes!("../flows/publish-request.compact.json");
#[derive(Clone)]
struct Card {
    x: f64,
    y: f64,
    title: String,
    kind: String,
    hint: String,
    path: String,
    line: i64,
    patch: Option<String>,
}
#[derive(Default)]
struct Scene {
    cards: Vec<Card>,
    edges: Vec<(usize, usize, String)>,
    labels: Vec<(f64, f64, String)>,
    bands: Vec<(f64, f64, f64)>,
    size: (i32, i32),
}
impl Scene {
    fn flow(f: Flow, positions: Positions) -> Self {
        let right = positions
            .values()
            .map(|(x, _)| x + 420)
            .max()
            .unwrap_or(1100);
        let bottom = positions
            .values()
            .map(|(_, y)| y + 260)
            .max()
            .unwrap_or(500);
        let mut scene = Self {
            size: (right, bottom),
            ..Self::default()
        };
        for region in &f.regions {
            let mut ys = f
                .nodes
                .iter()
                .filter(|n| n.region == region.id)
                .map(|n| positions[&n.id].1)
                .collect::<Vec<_>>();
            ys.sort_unstable();
            if let (Some(top), Some(bottom)) = (ys.first(), ys.last()) {
                scene
                    .bands
                    .push(((top - 42) as f64, (bottom + 204) as f64, right as f64));
                scene
                    .labels
                    .push((30., (top - 18) as f64, region.label.clone()));
            }
        }
        scene.bands.sort_by(|a, b| a.0.total_cmp(&b.0));
        let mut nodes = f.nodes;
        nodes.sort_by(|a, b| a.id.cmp(&b.id));
        let mut ids = std::collections::HashMap::new();
        for n in nodes {
            let (x, y) = positions[&n.id];
            ids.insert(n.id, scene.cards.len());
            scene.cards.push(Card {
                x: x as f64,
                y: y as f64,
                title: n.title,
                kind: n.kind,
                hint: n.hint,
                path: n.source.file,
                line: n.source.line as i64,
                patch: None,
            });
        }
        let mut edges = f.edges;
        edges.sort_by(|a, b| (&a.from, &a.to, &a.kind).cmp(&(&b.from, &b.to, &b.kind)));
        for e in edges {
            let (i, j) = (ids[&e.from], ids[&e.to]);
            let relation = &f.edge_types[&e.kind].relation;
            let (a, b) = (&scene.cards[i], &scene.cards[j]);
            let mut text = e.label;
            if matches!(relation.as_str(), "handoff" | "response" | "loop") {
                text = format!("{}: {text}", e.kind);
            }
            if e.origin == "uncertain" {
                text = format!("? UNVERIFIED {text}");
            }
            if !text.is_empty() {
                scene
                    .labels
                    .push(((a.x + W + b.x) / 2., (a.y + b.y) / 2. + H / 2. - 12., text));
            }
            scene.edges.push((i, j, relation.clone()));
        }
        scene
    }

    fn draw(&self, cr: &cairo::Context, root: &Path) -> Result<()> {
        let color = |rgb: (f64, f64, f64)| cr.set_source_rgb(rgb.0, rgb.1, rgb.2);
        let text = |x: f64, y: f64, value: &str, size: f64, rgb| -> Result<()> {
            color(rgb);
            cr.set_font_size(size);
            cr.move_to(x, y);
            cr.show_text(&value.replace('\0', "�"))?;
            Ok(())
        };
        let ink = (0.82, 0.85, 0.91);
        color((0.075, 0.09, 0.12));
        cr.paint()?;
        for (i, &(top, bottom, right)) in self.bands.iter().enumerate() {
            color([(0.105, 0.14, 0.19), (0.13, 0.12, 0.18), (0.10, 0.16, 0.15)][i % 3]);
            cr.rectangle(8., top, right - 16., bottom - top);
            cr.fill()?;
            color((0.32, 0.39, 0.47));
            cr.set_line_width(1.);
            cr.move_to(8., top);
            cr.line_to(right - 8., top);
            cr.move_to(8., bottom);
            cr.line_to(right - 8., bottom);
            cr.stroke()?;
        }
        for (i, j, kind) in &self.edges {
            let (a, b) = (&self.cards[*i], &self.cards[*j]);
            let pts = match kind.as_str() {
                "call" => vec![(a.x + W / 2., a.y + H), (b.x + W / 2., b.y)],
                "return" => vec![
                    (a.x + W, a.y + H / 2.),
                    (b.x + W / 2., a.y + H / 2.),
                    (b.x + W / 2., b.y + H),
                ],
                _ => vec![
                    (a.x + W, a.y + H / 2.),
                    (a.x + W + 24., a.y + H / 2.),
                    (a.x + W + 24., b.y + H / 2.),
                    (b.x, b.y + H / 2.),
                ],
            };
            color(if kind == "return" {
                (0.66, 0.8, 0.56)
            } else {
                (0.58, 0.74, 0.98)
            });
            cr.set_line_width(2.);
            if kind == "return" {
                cr.set_dash(&[7., 5.], 0.);
            }
            cr.move_to(pts[0].0, pts[0].1);
            for &(x, y) in &pts[1..] {
                cr.line_to(x, y);
            }
            cr.stroke()?;
            cr.set_dash(&[], 0.);
            let (x, y) = pts[pts.len() - 1];
            let (px, py) = pts[pts.len() - 2];
            let angle = (y - py).atan2(x - px);
            cr.move_to(x, y);
            for angle in [angle - 0.45, angle + 0.45] {
                cr.line_to(x - 10. * angle.cos(), y - 10. * angle.sin());
            }
            cr.close_path();
            cr.fill()?;
        }
        let (x1, y1, x2, y2) = cr.clip_extents()?;
        for (x, y, s) in &self.labels {
            text(*x, *y, s, 13., ink)?;
        }
        for n in &self.cards {
            let (x, y) = (n.x, n.y);
            if x > x2 || y > y2 || x + W < x1 || y + H < y1 {
                continue;
            }
            let (bg, accent, name) = match n.kind.as_str() {
                "condition" => ((0.25, 0.20, 0.10), (0.98, 0.73, 0.27), "IF · DECISION"),
                "throw" => ((0.29, 0.12, 0.16), (1., 0.46, 0.5), "THROW · STOP"),
                "return" => ((0.10, 0.23, 0.18), (0.39, 0.85, 0.62), "RETURN · DONE"),
                _ => ((0.12, 0.18, 0.27), (0.42, 0.68, 1.), "CALL · FUNCTION"),
            };
            color(bg);
            cr.rectangle(x, y, W, H);
            cr.fill()?;
            color(accent);
            cr.set_line_width(2.);
            cr.rectangle(x + 1., y + 1., W - 2., H - 2.);
            cr.stroke()?;
            if n.kind == "condition" {
                cr.move_to(x + 20., y + 9.);
                cr.line_to(x + 28., y + 17.);
                cr.line_to(x + 20., y + 25.);
                cr.line_to(x + 12., y + 17.);
                cr.close_path();
                cr.stroke()?;
            } else {
                cr.arc(x + 20., y + 17., 5., 0., std::f64::consts::TAU);
                cr.fill()?;
            }
            text(x + 36., y + 21., name, 10., accent)?;
            text(
                x + 12.,
                y + 46.,
                &n.title.chars().take(43).collect::<String>(),
                13.,
                ink,
            )?;
            let path = format!("{}:{}", root.join(&n.path).display(), n.line);
            let mut line = String::new();
            let mut ty = y + 70.;
            cr.set_font_size(11.);
            for ch in path.chars() {
                let old = line.len();
                line.push(ch);
                if old > 0 && cr.text_extents(&line)?.width() > W - 24. {
                    line.truncate(old);
                    text(x + 12., ty, &line, 11., (0.48, 0.74, 1.))?;
                    ty += 15.;
                    line.clear();
                    line.push(ch);
                }
            }
            text(x + 12., ty, &line, 11., (0.48, 0.74, 1.))?;
            text(x + 12., y + H - 15., &n.hint, 11., (0.64, 0.7, 0.78))?;
        }
        Ok(())
    }
}

pub struct Viewer {
    window: gtk::Window,
    root: PathBuf,
    path: RefCell<Option<PathBuf>>,
    scene: RefCell<Scene>,
    notice: gtk::Label,
    query: gtk::SearchEntry,
    area: gtk::DrawingArea,
    scroll: gtk::ScrolledWindow,
    original: RefCell<Option<gdk_pixbuf::Pixbuf>>,
    display: RefCell<Option<gdk_pixbuf::Pixbuf>>,
    zoom: Cell<f64>,
    drag: Cell<Option<(f64, f64)>>,
    diff_base: String,
    diff_request: RefCell<Option<Arc<AtomicBool>>>,
}

impl Viewer {
    pub fn new(root: PathBuf, path: Option<PathBuf>) -> Rc<Self> {
        let window = gtk::Window::new(gtk::WindowType::Toplevel);
        window.set_default_size(1320, 820);
        let body = vbox(10);
        body.set_margin(16);
        window.add(&body);
        let bar = hbox(8);
        body.pack_start(&bar, false, false, 0);
        let query = gtk::SearchEntry::new();
        query.set_placeholder_text(Some("Function name…"));
        bar.pack_start(&query, true, true, 0);
        let diff = button("PR diff overview");
        let search = button("Find function");
        let example = button("Publish example");
        let open = button("Open flow JSON");
        let collapse = button("Collapse all");
        for item in [&diff, &search, &example, &open, &collapse] {
            bar.pack_start(item, false, false, 0);
        }
        body.pack_start(&label("→ Continue at the same depth     ↓ Drill into a function     ⇡ Dashed: return to its caller",&[]),false,false,0);
        let notice = label("", &[]);
        notice.set_line_wrap(true);
        body.pack_start(&notice, false, false, 0);
        let area = gtk::DrawingArea::new();
        area.set_can_focus(true);
        area.add_events(
            gdk::EventMask::BUTTON_PRESS_MASK
                | gdk::EventMask::BUTTON_RELEASE_MASK
                | gdk::EventMask::POINTER_MOTION_MASK
                | gdk::EventMask::SCROLL_MASK
                | gdk::EventMask::SMOOTH_SCROLL_MASK,
        );
        let scroll = scroll(&area);
        body.pack_start(&scroll, true, true, 0);
        body.pack_start(&label("Click a card to expand/collapse. Click its blue filename to open Zed. Scroll horizontally for long paths. No files are changed.",&[]),false,false,0);
        let view = Rc::new(Self {
            window,
            root,
            path: RefCell::new(path),
            scene: Default::default(),
            notice,
            query,
            area,
            scroll,
            original: Default::default(),
            display: Default::default(),
            zoom: Cell::new(1.),
            drag: Default::default(),
            diff_request: Default::default(),
            diff_base: std::env::var("CODE_FLOW_DIFF_BASE")
                .unwrap_or_else(|_| "origin/main".into()),
        });
        view.area.connect_draw(clone!(@weak view => @default-return Propagation::Proceed, move |_,cr|{
            if let Some(image)=view.display.borrow().as_ref() {
                use gdk::prelude::GdkContextExt;
                cr.set_source_pixbuf(image,0.,0.);
                let _=cr.paint();
            } else {
                cr.scale(view.zoom.get(),view.zoom.get());
                if let Err(e)=view.scene.borrow().draw(cr,&view.root) {eprintln!("Drawing flow: {e}");}
            }
            Propagation::Stop
        }));
        view.window.connect_destroy(clone!(@weak view => move |_| {
            view.cancel_diff();
            gtk::main_quit();
        }));
        diff.connect_clicked(clone!(@weak view => move |_|view.diff()));
        search.connect_clicked(clone!(@weak view => move |_|view.search()));
        view.query
            .connect_activate(clone!(@weak view => move |_|view.search()));
        example.connect_clicked(clone!(@weak view => move |_| {
            view.path.replace(std::env::var_os("CODE_FLOW_FILE").map(PathBuf::from));
            view.example();
        }));
        open.connect_clicked(clone!(@weak view => move |_| view.choose_flow()));
        collapse.connect_clicked(
            clone!(@weak view => move |_| {view.cancel_diff(); view.reset_view();}),
        );
        view.connect_input();
        view.window.show_all();
        if std::env::var("CODE_FLOW_START_VIEW").as_deref() == Ok("diff") {
            view.diff();
        } else {
            view.example();
        }
        view
    }
    fn connect_input(self: &Rc<Self>) {
        let view = self;
        self.area.connect_scroll_event(clone!(@weak view => @default-return Propagation::Proceed, move |_,event| {
            if !event.state().intersects(gdk::ModifierType::CONTROL_MASK|gdk::ModifierType::META_MASK) {return Propagation::Proceed;}
            let dy=match event.direction() {
                gdk::ScrollDirection::Up=>-1.,gdk::ScrollDirection::Down=>1.,_=>event.delta().1,
            };
            if dy!=0. {view.set_zoom((view.zoom.get()*if dy<0.{1.15}else{1./1.15}).clamp(0.2,2.));}
            Propagation::Stop
        }));
        self.area.connect_button_press_event(clone!(@weak view => @default-return Propagation::Proceed, move |widget,event| {
            widget.grab_focus();
            if event.button()==2 {
                view.drag.set(Some(event.root()));
                if let Some(w)=widget.window(){w.set_cursor(gdk::Cursor::from_name(&widget.display(),"grabbing").as_ref());}
                return Propagation::Stop;
            }
            if event.button()!=1 {return Propagation::Proceed;}
            let (x,y)=event.position();let z=view.zoom.get();let (x,y)=(x/z,y/z);
            let hit=view.scene.borrow().cards.iter().rev().find(|n| x>=n.x&&x<=n.x+W&&y>=n.y&&y<=n.y+H).cloned();
            if let Some(n)=hit {
                if let Some(patch)=&n.patch { view.patch(&n.path,patch); }
                else if (55. ..=H-35.).contains(&(y-n.y)) {
                    let result=(|| -> Result<()> {
                        let root=view.root.canonicalize()?;let file=root.join(&n.path).canonicalize()?;
                        ensure!(file.starts_with(root)&&file.is_file(),"Source is outside the checkout");
                        let mut child=Command::new("zed").arg(format!("{}:{}",file.display(),n.line)).spawn()?;
                        std::thread::spawn(move || {let _=child.wait();});Ok(())
                    })();
                    if let Err(e)=result{view.notice.set_text(&format!("Could not open Zed: {e}"));}
                }
            }
            Propagation::Stop
        }));
        self.area.connect_motion_notify_event(
            clone!(@weak view => @default-return Propagation::Proceed, move |_,event| {
                let Some((x,y))=view.drag.get() else{return Propagation::Proceed};
                let p=event.root();view.pan(x-p.0,y-p.1);view.drag.set(Some(p));Propagation::Stop
            }),
        );
        self.area.connect_button_release_event(
            clone!(@weak view => @default-return Propagation::Proceed, move |widget,event| {
                if event.button()!=2{return Propagation::Proceed;}
                view.drag.set(None);if let Some(w)=widget.window() {w.set_cursor(None);}
                Propagation::Stop
            }),
        );
        self.window.connect_key_press_event(clone!(@weak view => @default-return Propagation::Proceed, move |_,event| {
            if view.window.focused_widget().is_some_and(|w|w.is::<gtk::Entry>()) ||
                event.state().intersects(gdk::ModifierType::CONTROL_MASK|gdk::ModifierType::MOD1_MASK) {return Propagation::Proceed;}
            match event.keyval().to_unicode().map(|c|c.to_ascii_lowercase()) {
                Some('w')=>view.pan(0.,-70.), Some('s')=>view.pan(0.,70.),Some('a')=>view.pan(-70.,0.),Some('d')=>view.pan(70.,0.),
                _=>return Propagation::Proceed,
            } Propagation::Stop
        }));
    }
    fn pan(&self, dx: f64, dy: f64) {
        for (adj, d) in [
            (self.scroll.hadjustment(), dx),
            (self.scroll.vadjustment(), dy),
        ] {
            adj.set_value(adj.value() + d);
        }
    }
    fn set_zoom(&self, z: f64) {
        self.zoom.set(z);
        let (w, h) = self.scene.borrow().size;
        let width = (w as f64 * z).round_ties_even().max(1.) as i32;
        let height = (h as f64 * z).round_ties_even().max(1.) as i32;
        let scaled = self.original.borrow().as_ref().and_then(|image| {
            if z == 1. {
                Some(image.clone())
            } else if i64::from(width) * i64::from(height) <= 24_000_000 {
                image.scale_simple(width, height, gdk_pixbuf::InterpType::Bilinear)
            } else {
                None
            }
        });
        self.display.replace(scaled);
        self.area.set_size_request(width, height);
        self.area.queue_draw();
        self.window
            .set_title(&format!("Code Flow — Prototype · {:.0}%", z * 100.));
    }
    fn reset_view(&self) {
        // The original flow renderer rasterized at 100% and scaled that image
        // bilinearly. Cache it for pans; large diagrams use clipped vector drawing.
        let raster = (|| -> Result<Option<gdk_pixbuf::Pixbuf>> {
            let scene = self.scene.borrow();
            let (w, h) = scene.size;
            if w <= 0
                || h <= 0
                || w > 30000
                || h > 10000
                || i64::from(w) * i64::from(h) > 16_000_000
            {
                return Ok(None);
            }
            let surface = cairo::ImageSurface::create(cairo::Format::ARgb32, w, h)?;
            scene.draw(&cairo::Context::new(&surface)?, &self.root)?;
            Ok(gdk::pixbuf_get_from_surface(&surface, 0, 0, w, h))
        })();
        self.original.replace(raster.unwrap_or_else(|e| {
            eprintln!("Rasterizing flow: {e}");
            None
        }));
        self.set_zoom(1.);
        let first = self
            .scene
            .borrow()
            .cards
            .iter()
            .min_by_key(|n| (n.x as i32, n.y as i32))
            .map(|n| (n.x, n.y));
        if let Some((x, y)) = first {
            let scroll = self.scroll.clone();
            let area = self.area.clone();
            glib::idle_add_local_once(move || {
                scroll.hadjustment().set_value((x - 30.).max(0.));
                scroll.vadjustment().set_value((y - 65.).max(0.));
                area.grab_focus();
            });
        }
    }
    fn example(&self) {
        self.cancel_diff();
        let read = match self.path.borrow().as_ref() {
            Some(p) => flow_model::read(p, &self.root),
            None => flow_model::parse(EXAMPLE, &self.root),
        };
        match read {
            Ok((flow, positions, warnings)) => {
                self.notice.set_text(&format!("{} · automatic layout · {}",flow.title,if warnings.is_empty(){
                    "Source evidence unchanged; flow meaning is agent-reviewed, not parser-proven.".into()
                }else{warnings.join("; ")}));
                self.scene.replace(Scene::flow(flow, positions));
            }
            Err(e) => {
                self.notice.set_text(&format!("Cannot display flow: {e}"));
                self.scene.replace(Scene {
                    size: (1100, 500),
                    ..Default::default()
                });
            }
        }
        self.reset_view();
    }
    fn choose_flow(&self) {
        let dialog = gtk::FileChooserDialog::with_buttons(
            Some("Open a flow JSON"),
            Some(&self.window),
            gtk::FileChooserAction::Open,
            &[
                ("Cancel", gtk::ResponseType::Cancel),
                ("Open", gtk::ResponseType::Ok),
            ],
        );
        if let Some(folder) = self
            .path
            .borrow()
            .as_ref()
            .and_then(|p| p.parent().map(Path::to_owned))
        {
            dialog.set_current_folder(folder);
        }
        if dialog.run() == gtk::ResponseType::Ok {
            self.path.replace(dialog.filename());
            self.example();
        }
        dialog.close();
    }
    fn search(&self) {
        let query = self.query.text().trim().to_owned();
        if query.is_empty() {
            return;
        }
        let rows = (|| -> Result<Vec<(String, String, i64)>> {
            let folder = self.root.join(".tokensave");
            let meta: Value =
                serde_json::from_slice(&std::fs::read(folder.join("branch-meta.json"))?)?;
            let path = folder
                .join(
                    meta["branches"]["main"]["db_file"]
                        .as_str()
                        .context("Missing tokensave index for main")?,
                )
                .canonicalize()?;
            ensure!(
                path.starts_with(folder.canonicalize()?),
                "Index escapes .tokensave"
            );
            let db = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
            let mut q=db.prepare("SELECT name,file_path,start_line FROM nodes WHERE name LIKE ?1 AND kind IN ('function','arrow_function','method','const') ORDER BY (name=?2) DESC, name,file_path LIMIT 60")?;
            let rows = q
                .query_map([&format!("%{query}%"), &query], |r| {
                    Ok((r.get(0)?, r.get(1)?, r.get(2)?))
                })?
                .collect::<rusqlite::Result<_>>()?;
            Ok(rows)
        })();
        let rows = match rows {
            Ok(rows) => rows,
            Err(e) => {
                self.notice
                    .set_text(&format!("Cannot search the saved index: {e}"));
                return;
            }
        };
        let dialog = gtk::Dialog::with_buttons(
            Some("Choose a function"),
            Some(&self.window),
            gtk::DialogFlags::MODAL,
            &[("Cancel", gtk::ResponseType::Cancel)],
        );
        dialog.set_default_size(950, 450);
        let store = gtk::ListStore::new(&[
            String::static_type(),
            String::static_type(),
            u32::static_type(),
        ]);
        for (i, (name, path, line)) in rows.iter().enumerate() {
            store.insert_with_values(
                None,
                &[(0, name), (1, &format!("{path}:{line}")), (2, &(i as u32))],
            );
        }
        let tree = gtk::TreeView::with_model(&store);
        for (i, name) in ["Function", "Source"].iter().enumerate() {
            let col = gtk::TreeViewColumn::new();
            col.set_title(name);
            let renderer = gtk::CellRendererText::new();
            tree.append_column(&col);
            gtk::prelude::TreeViewColumnExt::pack_start(&col, &renderer, true);
            gtk::prelude::TreeViewColumnExt::add_attribute(&col, &renderer, "text", i as i32);
        }
        tree.connect_row_activated(
            clone!(@weak dialog => move |_,_,_|dialog.response(gtk::ResponseType::Ok)),
        );
        dialog
            .content_area()
            .pack_start(&scroll(&tree), true, true, 0);
        dialog.show_all();
        if dialog.run() == gtk::ResponseType::Ok {
            if let Some((model, it)) = tree.selection().selected() {
                if let Ok(index) = model.value(&it, 2).get::<u32>() {
                    self.cancel_diff();
                    let (name, path, line) = &rows[index as usize];
                    if name == "publishDocumentRequest"
                        && path.ends_with("api-functions/documents/publishDocumentRequest.ts")
                    {
                        self.example();
                    } else {
                        self.notice.set_text("INDEXED VIEW · Calls are ordered by source line. Conditions, await/async behavior and actual returns are not resolved. Dashed arrows illustrate return-to-caller, not proven runtime behavior.");
                        self.scene.replace(Scene {
                            cards: vec![Card {
                                x: 30.,
                                y: 60.,
                                title: name.clone(),
                                kind: "call".into(),
                                hint: "Source only · execution flow not verified".into(),
                                path: path.clone(),
                                line: *line,
                                patch: None,
                            }],
                            size: (1100, 350),
                            labels: vec![(
                                30.,
                                38.,
                                "Selected function · no verified execution flow".into(),
                            )],
                            ..Default::default()
                        });
                        self.reset_view();
                    }
                }
            }
        }
        dialog.close();
    }
    fn patch(&self, path: &str, patch: &str) {
        let dialog = gtk::Dialog::with_buttons(
            Some(&format!("Diff · {path}")),
            Some(&self.window),
            gtk::DialogFlags::DESTROY_WITH_PARENT,
            &[("Close", gtk::ResponseType::Close)],
        );
        dialog.set_default_size(1100, 760);
        let text = gtk::TextView::new();
        text.set_editable(false);
        text.set_monospace(true);
        text.buffer().unwrap().set_text(if patch.is_empty() {
            "No diff for this file."
        } else {
            patch
        });
        dialog
            .content_area()
            .pack_start(&scroll(&text), true, true, 0);
        dialog.connect_response(|d, _| d.close());
        dialog.show_all();
    }
    fn cancel_diff(&self) {
        if let Some(request) = self.diff_request.take() {
            request.store(true, Ordering::Release);
        }
    }

    fn diff(self: &Rc<Self>) {
        self.cancel_diff();
        self.notice.set_text("Reading PR diff…");
        let cancel = Arc::new(AtomicBool::new(false));
        self.diff_request.replace(Some(cancel.clone()));
        let root = self.root.clone();
        let base = self.diff_base.clone();
        let request = cancel.clone();
        let weak = Rc::downgrade(self);
        let task = gio::spawn_blocking(move || diff_snapshot(&root, &base, &request));
        glib::MainContext::default().spawn_local(async move {
            let result = task.await.unwrap_or_else(|_| Err(anyhow!("Git diff worker stopped unexpectedly.")));
            let Some(view) = weak.upgrade() else { return };
            if cancel.load(Ordering::Acquire) { return; }
            view.diff_request.take();
            match result {
                Ok(scene) => {
                    view.scene.replace(scene);
                    view.notice.set_text("PR DIFF · left to right: DBTypes → Frontend → Shared → Backend. Click a card to read its diff. Set CODE_FLOW_DIFF_BASE to compare another base.");
                }
                Err(e) => {
                    view.scene.replace(Scene {size:(1790,90),..Scene::default()});
                    view.notice.set_text(&format!("Cannot read diff against {}: {e}",view.diff_base));
                    eprintln!("Cannot read flow diff against {}: {e}",view.diff_base);
                }
            }
            view.reset_view();
        });
    }
}

// --raw -z prints the entire name/status table before the patches. Reading
// both in one Git invocation keeps previews on the same working-tree snapshot
// and avoids a repository scan for every card.
fn diff_snapshot(root: &Path, base: &str, cancel: &AtomicBool) -> Result<Scene> {
    ensure!(
        !base.is_empty() && !base.starts_with('-'),
        "Invalid comparison base"
    );
    let raw = crate::backend::run(
        root,
        "git",
        &[
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--raw",
            "-z",
            "--patch",
            "--submodule=short",
            "--unified=3",
            base,
            "--",
        ],
        Some(cancel),
    )
    .map_err(anyhow::Error::msg)?;
    let mut counts = [0; 4];
    let mut scene = Scene {
        size: (1790, 90),
        ..Default::default()
    };
    for (i, name) in ["DBTYPES", "FRONTEND", "SHARED/API", "BACKEND"]
        .iter()
        .enumerate()
    {
        scene
            .labels
            .push((30. + i as f64 * 440., 48., name.to_string()));
    }
    if raw.is_empty() {
        return Ok(scene);
    }
    let end = raw
        .windows(2)
        .position(|pair| pair == b"\0\0")
        .context("Incomplete Git patch table")?;
    let mut fields = raw[..end].split(|&b| b == 0);
    let patches = String::from_utf8_lossy(&raw[end + 2..]);
    // A diff hunk prefixes every content line with a marker. Only real
    // section headers can begin a line with "diff --git ".
    let mut offsets = patches
        .match_indices("\ndiff --git ")
        .map(|(i, _)| i + 1)
        .collect::<Vec<_>>();
    if !patches.is_empty() {
        ensure!(
            patches.starts_with("diff --git "),
            "Missing first Git patch"
        );
        offsets.insert(0, 0);
    }
    offsets.push(patches.len());
    let mut ranges = offsets.windows(2).peekable();
    while let Some(header) = fields.next() {
        if cancel.load(Ordering::Acquire) {
            return Err(anyhow!("Diff cancelled."));
        }
        let status = std::str::from_utf8(header)?
            .split_ascii_whitespace()
            .last()
            .context("Missing Git status")?;
        let mut name = fields.next().context("Missing Git path")?;
        if status.starts_with(['R', 'C']) {
            name = fields.next().context("Missing Git rename target")?;
        }
        let path = String::from_utf8_lossy(name);
        let lower = path.to_lowercase();
        let column = [
            &["dbtypes", "db-types", "types"][..],
            &["frontend", "frontend-web", "web"],
            &["shared", "contract", "api"],
            &["backend", "service", "functions"],
        ]
        .iter()
        .position(|hints| hints.iter().any(|hint| lower.contains(hint)))
        .unwrap_or(2);
        let range = ranges.next().context("Missing patch for changed file")?;
        let (start, mut end) = (range[0], range[1]);
        // Git represents a regular-file/symlink/gitlink type change with
        // separate delete and create patch sections for the same path.
        if status.starts_with('T')
            && patches[start..end]
                .lines()
                .nth(1)
                .is_some_and(|s| s.starts_with("deleted file mode "))
        {
            if let Some(next) = ranges.peek() {
                let created = &patches[next[0]..next[1]];
                if created.lines().next() == patches[start..end].lines().next()
                    && created
                        .lines()
                        .nth(1)
                        .is_some_and(|s| s.starts_with("new file mode "))
                {
                    end = ranges.next().unwrap()[1];
                }
            }
        }
        scene.cards.push(Card {
            x: 30. + column as f64 * 440.,
            y: (70 + counts[column] * 208) as f64,
            kind: if status == "M" { "condition" } else { "call" }.into(),
            title: format!("{status}  {path}"),
            hint: "Click to open diff".into(),
            path: path.into_owned(),
            line: 1,
            patch: Some(patches[start..end].to_owned()),
        });
        counts[column] += 1;
        scene.size.1 = scene.size.1.max(82 + counts[column] * 208);
    }
    ensure!(ranges.next().is_none(), "Unmatched Git patch sections");
    Ok(scene)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use std::fs;
    use std::os::unix::fs::symlink;

    #[test]
    fn snapshot_matches_per_file_git_patches_including_renames_type_changes_and_conflicts() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        let command = |args: &[&str]| {
            Command::new("git")
                .args([
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=t@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    "--literal-pathspecs",
                ])
                .args(args)
                .current_dir(root)
                .output()
                .unwrap()
        };
        let git = |args: &[&str]| {
            let out = command(args);
            assert!(
                out.status.success(),
                "{args:?}: {}",
                String::from_utf8_lossy(&out.stderr)
            );
            out.stdout
        };
        git(&["init", "-q", "-b", "main"]);
        git(&["config", "diff.renames", "true"]);
        for (name, body) in [
            ("conflict", "initial\n"),
            ("change-type", "original\n"),
            ("deleted", "deleted\n"),
            ("old-name", "renamed\n"),
        ] {
            fs::write(root.join(name), body).unwrap();
        }
        git(&["add", "-A"]);
        git(&["commit", "-qm", "Base"]);
        let cancel = AtomicBool::new(false);
        assert!(diff_snapshot(root, "HEAD", &cancel)
            .unwrap()
            .cards
            .is_empty());
        git(&["switch", "-qc", "other"]);
        fs::write(root.join("conflict"), "theirs\n").unwrap();
        git(&["commit", "-qam", "Other"]);
        git(&["switch", "-q", "main"]);
        fs::write(root.join("conflict"), "ours\n").unwrap();
        git(&["commit", "-qam", "Main"]);
        assert!(!command(&["merge", "other"]).status.success());
        fs::remove_file(root.join("change-type")).unwrap();
        symlink("target", root.join("change-type")).unwrap();
        fs::remove_file(root.join("deleted")).unwrap();
        let unusual = "new [literal]\nfile";
        fs::rename(root.join("old-name"), root.join(unusual)).unwrap();
        fs::write(root.join("binary"), [0, 3, 2]).unwrap();
        git(&[
            "add",
            "-A",
            "--",
            "binary",
            "change-type",
            "deleted",
            "old-name",
            unusual,
        ]);
        let scene = diff_snapshot(root, "HEAD", &cancel).unwrap();
        let by_path = scene
            .cards
            .iter()
            .map(|c| (c.path.as_str(), c))
            .collect::<BTreeMap<_, _>>();
        assert_eq!(by_path.len(), 5);
        assert!(by_path[unusual].title.starts_with("R"));
        assert!(by_path["change-type"].title.starts_with("T"));
        for (&path, card) in &by_path {
            let mut args = vec![
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--unified=3",
                "HEAD",
                "--",
                path,
            ];
            if path == unusual {
                args.push("old-name");
            }
            assert_eq!(
                card.patch.as_deref().unwrap().as_bytes(),
                git(&args),
                "patch for {path}"
            );
        }
        assert!(by_path["conflict"]
            .patch
            .as_ref()
            .unwrap()
            .contains("<<<<<<< HEAD"));
        assert!(diff_snapshot(root, "HEAD", &AtomicBool::new(true)).is_err());
    }
}
