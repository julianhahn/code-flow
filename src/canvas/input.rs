use super::*;
use glib::Propagation::{Proceed, Stop};
use std::time::Duration;

#[derive(Clone, Copy)]
pub(super) struct Drag {
    root: (f64, f64),
    viewport: (f64, f64),
}

impl Canvas {
    fn viewport(&self) -> (f64, f64) {
        (
            self.scroll.hadjustment().value(),
            self.scroll.vadjustment().value(),
        )
    }
    fn pan_to(&self, (x, y): (f64, f64)) {
        self.scroll.hadjustment().set_value(x);
        self.scroll.vadjustment().set_value(y);
    }
    fn queue_pan(self: &Rc<Self>, point: (f64, f64)) {
        self.pending_pan.set(Some(point));
        if self.pan_queued.replace(true) {
            return;
        }
        let weak = Rc::downgrade(self);
        self.scroll.add_tick_callback(move |_, _| {
            if let Some(c) = weak.upgrade() {
                if let Some(point) = c.pending_pan.take() {
                    c.pan_to(point);
                }
                if c.drag.get().is_some() {
                    return glib::ControlFlow::Continue;
                }
                c.pan_queued.set(false);
            }
            glib::ControlFlow::Break
        });
    }

    pub(super) fn bind_input(self: &Rc<Self>, w: &impl IsA<gtk::Widget>, file: Option<usize>) {
        w.add_events(
            gdk::EventMask::BUTTON_PRESS_MASK
                | gdk::EventMask::BUTTON_RELEASE_MASK
                | gdk::EventMask::POINTER_MOTION_MASK
                | gdk::EventMask::SCROLL_MASK
                | gdk::EventMask::SMOOTH_SCROLL_MASK,
        );
        let weak = Rc::downgrade(self);
        w.connect_button_press_event(move |w, e| {
            let Some(c) = weak.upgrade() else {
                return Proceed;
            };
            match e.button() {
                1 => {
                    if let Some(i) = file {
                        if e.event_type() == gdk::EventType::DoubleButtonPress {
                            c.center_on_file(&c.files[i].path, 1.0);
                            return Stop;
                        }
                        c.select_file(i);
                    }
                }
                2 => {
                    c.drag.set(Some(Drag {
                        root: e.root(),
                        viewport: c.viewport(),
                    }));
                    w.grab_add();
                    return Stop;
                }
                _ => (),
            }
            Proceed
        });
        let weak = Rc::downgrade(self);
        w.connect_motion_notify_event(move |_, e| {
            let Some(c) = weak.upgrade() else {
                return Proceed;
            };
            let Some(drag) = c.drag.get() else {
                return Proceed;
            };
            let (x, y) = e.root();
            c.queue_pan((
                drag.viewport.0 + drag.root.0 - x,
                drag.viewport.1 + drag.root.1 - y,
            ));
            Stop
        });
        let weak = Rc::downgrade(self);
        w.connect_button_release_event(move |w, e| {
            let Some(c) = weak.upgrade() else {
                return Proceed;
            };
            if e.button() == 2 && c.drag.take().is_some() {
                if let Some(point) = c.pending_pan.take() {
                    c.pan_to(point);
                }
                if w.has_grab() {
                    w.grab_remove();
                }
                return Stop;
            }
            Proceed
        });
        let weak = Rc::downgrade(self);
        w.connect_scroll_event(move |_, e| {
            let Some(c) = weak.upgrade() else {
                return Proceed;
            };
            if !e.state().intersects(
                gdk::ModifierType::CONTROL_MASK
                    | gdk::ModifierType::META_MASK
                    | gdk::ModifierType::SUPER_MASK,
            ) {
                // GtkScrolledWindow owns precise two-axis deltas and touchpad momentum.
                return Proceed;
            }
            let delta = match e.direction() {
                gdk::ScrollDirection::Up => -1.0,
                gdk::ScrollDirection::Smooth => e.delta().1,
                _ => 1.0,
            };
            if delta != 0.0 {
                c.queue_zoom(c.zoom_target() * 1.08f64.powf(-delta.clamp(-5.0, 5.0)));
            }
            Stop
        });
    }
    fn zoom_target(&self) -> f64 {
        self.pending_zoom.get().unwrap_or(self.zoom.get())
    }
    fn cancel_zoom(&self) {
        if let Some(id) = self.zoom_timer.take() {
            id.remove();
        }
        self.pending_zoom.set(None);
    }
    fn queue_zoom(self: &Rc<Self>, target: f64) {
        self.cancel_zoom();
        let target = target.clamp(0.15, 2.5);
        self.pending_zoom.set(Some(target));
        self.info.set_text(&format!(
            "{}% · Zooming…",
            (target * 100.0).round_ties_even() as i32
        ));
        let weak = Rc::downgrade(self);
        self.zoom_timer.replace(Some(glib::timeout_add_local_once(
            Duration::from_millis(120),
            move || {
                if let Some(c) = weak.upgrade() {
                    c.zoom_timer.take();
                    c.pending_zoom.set(None);
                    c.set_zoom(target);
                }
            },
        )));
    }
    pub fn set_zoom(self: &Rc<Self>, target: f64) {
        self.cancel_zoom();
        let z = target.clamp(0.15, 2.5);
        let ratio = z / self.zoom.replace(z);
        let center = |adj: gtk::Adjustment| {
            (adj.value() + adj.page_size() * 0.5) * ratio - adj.page_size() * 0.5
        };
        let point = (
            center(self.scroll.hadjustment()),
            center(self.scroll.vadjustment()),
        );
        self.render();
        self.after_layout(move |c| c.pan_to(point));
    }
    pub fn fit_all(self: &Rc<Self>) {
        self.cancel_zoom();
        let (w, h) = self.board.size();
        let pw = self.scroll.hadjustment().page_size();
        let ph = self.scroll.vadjustment().page_size();
        if w == 0 || h == 0 || pw <= 0.0 || ph <= 0.0 {
            return;
        }
        self.set_zoom(self.zoom.get() * (pw / w as f64).min(ph / h as f64) * 0.98);
        self.after_layout(|c| c.pan_to((0.0, 0.0)));
    }
    pub(super) fn invalidate_camera_restore(&self) {
        self.generation.set(self.generation.get().wrapping_add(1));
        self.camera_updates.borrow_mut().clear();
    }

    pub(super) fn select_file(&self, index: usize) {
        self.invalidate_camera_restore();
        self.navigation.borrow_mut().reset_anchor();
        self.active.set(Some(index));
        self.update_location();
    }

    pub fn jump_to_file(self: &Rc<Self>, path: &str) {
        let Some(&i) = self.paths.get(path) else {
            return;
        };
        if !self.cards.borrow().iter().any(|card| card.index == i) {
            return;
        }
        self.select_file(i);
        self.after_layout(move |c| {
            let rect = c
                .cards
                .borrow()
                .iter()
                .find(|p| p.index == i)
                .map(|p| p.rect);
            if let Some(r) = rect {
                let a = c.scroll.vadjustment();
                let (w, h) = c.board.size();
                let end = r.y + a.page_size();
                c.board.set_size(w, h.max(end.round_ties_even() as u32));
                a.set_upper(a.upper().max(end));
                c.pan_to((r.x, r.y));
            }
        });
    }
    pub(super) fn center(&self, index: usize) {
        let rect = self
            .cards
            .borrow()
            .iter()
            .find(|c| c.index == index)
            .map(|c| c.rect);
        if let Some(r) = rect {
            let value = |a: gtk::Adjustment, p: f64, s: f64| {
                let page = a.page_size();
                (if s > page {
                    p - 20.0
                } else {
                    p + s / 2.0 - page / 2.0
                })
                .max(0.0)
            };
            self.pan_to((
                value(self.scroll.hadjustment(), r.x, r.w),
                value(self.scroll.vadjustment(), r.y, r.h),
            ));
            self.active.set(Some(index));
            self.update_location();
        }
    }
    pub fn center_on_file(self: &Rc<Self>, path: &str, minimum_zoom: f64) {
        let Some(&index) = self.paths.get(path) else {
            return;
        };
        self.select_file(index);
        if self.zoom.get() < minimum_zoom {
            self.set_zoom(minimum_zoom);
            self.after_layout(move |c| c.center(index));
        } else {
            self.center(index);
        }
    }
    pub fn navigate(&self, direction: &str) {
        let Some(current) = self.active.get() else {
            return;
        };
        let target = self.navigation.borrow_mut().step(current, direction);
        if let Some(index) = target {
            self.invalidate_camera_restore();
            self.center(index);
        }
    }
    pub fn scroll_by(self: &Rc<Self>, direction: &str) {
        let (a, sign) = match direction {
            "up" => (self.scroll.vadjustment(), -1.0),
            "down" => (self.scroll.vadjustment(), 1.0),
            "left" => (self.scroll.hadjustment(), -1.0),
            "right" => (self.scroll.hadjustment(), 1.0),
            _ => return,
        };
        let pan = move |_: &Canvas| a.set_value(a.value() + sign * 0.4 * a.page_size());
        if self.camera_updates.borrow().is_empty() {
            pan(self);
        } else {
            self.after_layout(pan);
        }
    }
}

pub(super) enum Pinch {
    Gtk {
        _gesture: gtk::GestureZoom,
    },
    #[cfg(target_os = "macos")]
    Quartz {
        _filter: quartz::Filter,
    },
}

pub(super) fn pinch(c: &Rc<Canvas>) -> Pinch {
    #[cfg(target_os = "macos")]
    if let Some(filter) = quartz::Filter::new(c) {
        return Pinch::Quartz { _filter: filter };
    }
    let gesture = gtk::GestureZoom::new(&c.scroll);
    gesture.set_propagation_phase(gtk::PropagationPhase::Capture);
    let start = Rc::new(Cell::new(1.0));
    gesture
        .connect_begin(glib::clone!(@weak c,@strong start=>move |_,_|start.set(c.zoom_target())));
    gesture
        .connect_scale_changed(glib::clone!(@weak c=>move|_,scale|c.queue_zoom(start.get()*scale)));
    Pinch::Gtk { _gesture: gesture }
}

#[cfg(target_os = "macos")]
mod quartz {
    use super::*;
    use glib::translate::ToGlibPtr;
    use std::{ffi::c_void, ptr, rc::Weak};

    // Quartz GTK3 never converts NSEventTypeMagnify into a GtkGestureZoom. GDK's
    // native filter is scoped to the live map window and passes all wheel events on.
    #[link(name = "objc")]
    extern "C" {
        fn objc_msgSend();
        fn sel_registerName(name: *const libc::c_char) -> *mut c_void;
    }
    type WindowFn = unsafe extern "C" fn(*mut gdk::ffi::GdkWindow) -> *mut c_void;
    struct Data {
        canvas: Weak<Canvas>,
        nswindow: WindowFn,
        kind: *mut c_void,
        window: *mut c_void,
        scale: *mut c_void,
    }
    pub(in crate::canvas) struct Filter {
        data: Box<Data>,
    }
    impl Filter {
        pub(super) fn new(c: &Rc<Canvas>) -> Option<Self> {
            if !gdk::Display::default()?.type_().name().contains("Quartz") {
                return None;
            }
            unsafe {
                // A macOS build of GTK can use the X11 backend too.
                let symbol = libc::dlsym(
                    libc::RTLD_DEFAULT,
                    c"gdk_quartz_window_get_nswindow".as_ptr(),
                );
                if symbol.is_null() {
                    return None;
                }
                let mut data = Box::new(Data {
                    canvas: Rc::downgrade(c),
                    nswindow: std::mem::transmute::<*mut c_void, WindowFn>(symbol),
                    kind: sel_registerName(c"type".as_ptr()),
                    window: sel_registerName(c"window".as_ptr()),
                    scale: sel_registerName(c"magnification".as_ptr()),
                });
                gdk::ffi::gdk_window_add_filter(
                    ptr::null_mut(),
                    Some(filter),
                    (&mut *data as *mut Data).cast(),
                );
                Some(Self { data })
            }
        }
    }
    impl Drop for Filter {
        fn drop(&mut self) {
            unsafe {
                gdk::ffi::gdk_window_remove_filter(
                    ptr::null_mut(),
                    Some(filter),
                    (&mut *self.data as *mut Data).cast(),
                );
            }
        }
    }
    unsafe extern "C" fn filter(
        native: *mut gdk::ffi::GdkXEvent,
        _: *mut gdk::ffi::GdkEvent,
        p: *mut c_void,
    ) -> i32 {
        let data = &*p.cast::<Data>();
        let integer: unsafe extern "C" fn(*mut c_void, *mut c_void) -> usize =
            std::mem::transmute(objc_msgSend as *const ());
        if native.is_null() || integer(native.cast(), data.kind) != 30 {
            return gdk::ffi::GDK_FILTER_CONTINUE;
        }
        let Some(c) = data.canvas.upgrade() else {
            return gdk::ffi::GDK_FILTER_CONTINUE;
        };
        if !c.scroll.is_mapped() {
            return gdk::ffi::GDK_FILTER_CONTINUE;
        }
        let Some(window) = c.scroll.toplevel().and_then(|w| w.window()) else {
            return gdk::ffi::GDK_FILTER_CONTINUE;
        };
        let ns = (data.nswindow)(window.to_glib_none().0);
        if ns as usize != integer(native.cast(), data.window) {
            return gdk::ffi::GDK_FILTER_CONTINUE;
        }
        let (mut x, mut y) = (0, 0);
        gtk::ffi::gtk_widget_get_pointer(
            c.scroll.upcast_ref::<gtk::Widget>().to_glib_none().0,
            &mut x,
            &mut y,
        );
        if x < 0 || y < 0 || x >= c.scroll.allocated_width() || y >= c.scroll.allocated_height() {
            return gdk::ffi::GDK_FILTER_CONTINUE;
        }
        let floating: unsafe extern "C" fn(*mut c_void, *mut c_void) -> f64 =
            std::mem::transmute(objc_msgSend as *const ());
        c.queue_zoom(c.zoom_target() * (1.0 + floating(native.cast(), data.scale)).max(0.01));
        gdk::ffi::GDK_FILTER_REMOVE
    }
}
