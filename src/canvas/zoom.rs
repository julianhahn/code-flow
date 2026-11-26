//! Animate the existing map while preparing the next interactive GTK layout.
//! The backing image covers the visible part of the board, not its full extent.
use super::*;
mod preview;
use preview::{LastFrame, Picture};
use std::time::{Duration, Instant};

const EASE_US: f64 = 110_000.0;
type Point = (f64, f64);

#[derive(Clone, Copy)]
enum Focus {
    Point(Point),
    Fit,
    Card(usize),
}

enum PendingInput {
    Zoom {
        zoom: f64,
        focus: Focus,
        gesture: bool,
    },
    Action(CameraUpdate),
}

#[derive(Clone, Copy, Debug)]
struct View {
    ratio: f64,
    vertical: f64,
    x: f64,
    y: f64,
}

impl View {
    fn board_point(self, (x, y): Point) -> Point {
        ((self.x + x) / self.ratio, (self.y + y) / self.vertical)
    }

    fn towards(self, to: Self, t: f64) -> Self {
        let lerp = |a: f64, b: f64| a + (b - a) * t;
        Self {
            ratio: lerp(self.ratio, to.ratio),
            vertical: lerp(self.vertical, to.vertical),
            x: lerp(self.x, to.x),
            y: lerp(self.y, to.y),
        }
    }

    fn measured_about(self, world: Point, x_scale: f64, y_scale: f64) -> Self {
        Self {
            x: self.x + world.0 * (x_scale - self.ratio),
            y: self.y + world.1 * (y_scale - self.vertical),
            ratio: x_scale,
            vertical: y_scale,
        }
    }

    fn visible(self, page: Point) -> Rect {
        Rect {
            x: self.x / self.ratio,
            y: self.y / self.vertical,
            w: page.0 / self.ratio,
            h: page.1 / self.vertical,
        }
    }
}

#[derive(Default)]
pub(super) struct ZoomAnimation {
    motion: RefCell<Option<Transition>>,
    handoff: RefCell<Option<LastFrame>>,
    settling: Cell<bool>,
    ticking: Cell<bool>,
    actions: RefCell<Vec<PendingInput>>,
    drag: Cell<Option<Point>>,
}

impl ZoomAnimation {
    fn defer_zoom(&self, zoom: f64, focus: Focus, gesture: bool) {
        // Wheel pulses can arrive while GTK maps the new board. Keep the
        // latest accumulated destination without replaying stale layouts.
        // A click or navigation key between pulses keeps its place.
        let input = PendingInput::Zoom {
            zoom,
            focus,
            gesture,
        };
        let mut pending = self.actions.borrow_mut();
        if let Some(last @ PendingInput::Zoom { .. }) = pending.last_mut() {
            *last = input;
        } else {
            pending.push(input);
        }
    }
}

struct Transition {
    from: View,
    current: View,
    target: View,
    zoom: f64,
    started: i64,
    duration: f64,
    focus: Focus,
    // Keep the same fractional position in a card when real font rounding and
    // GTK minimum sizes make the final layout differ from a pure scale.
    card_anchor: Option<(usize, Point)>,
    picture: Option<Picture>,
    layout: Option<render::Layout>,
    ready: bool,
    measure_pivot: bool,
}

impl Canvas {
    fn page_size(&self) -> Point {
        (
            self.scroll.hadjustment().page_size().max(1.0),
            self.scroll.vadjustment().page_size().max(1.0),
        )
    }

    pub(super) fn zoom_target(&self) -> f64 {
        self.zoom_animation
            .actions
            .borrow()
            .iter()
            .rev()
            .find_map(|input| match input {
                PendingInput::Zoom { zoom, .. } => Some(*zoom),
                PendingInput::Action(_) => None,
            })
            .unwrap_or_else(|| {
                self.zoom_animation
                    .motion
                    .borrow()
                    .as_ref()
                    .map_or(self.zoom.get(), |m| m.zoom)
            })
    }

    fn card_at(&self, point: Point) -> Option<(usize, Point)> {
        self.cards.borrow().iter().find_map(|card| {
            let r = card.rect;
            let offset = ((point.0 - r.x) / r.w, (point.1 - r.y) / r.h);
            ((0.0..=1.0).contains(&offset.0) && (0.0..=1.0).contains(&offset.1))
                .then_some((card.index, offset))
        })
    }

    pub(super) fn wheel_zoom(self: &Rc<Self>, zoom: f64, anchor: Point) {
        self.request_zoom(zoom, Focus::Point(anchor), true);
    }
    pub fn set_zoom(self: &Rc<Self>, zoom: f64) {
        let page = self.page_size();
        self.request_zoom(zoom, Focus::Point((0.5 * page.0, 0.5 * page.1)), false);
    }
    pub fn fit_all(self: &Rc<Self>) {
        let (w, h) = self.board.borrow().size();
        let (pw, ph) = self.page_size();
        if w > 0 && h > 0 {
            self.request_zoom(
                self.zoom.get() * (pw / w as f64).min(ph / h as f64) * 0.98,
                Focus::Fit,
                false,
            );
        }
    }
    pub(super) fn zoom_to_card(self: &Rc<Self>, zoom: f64, file: usize) {
        self.request_zoom(zoom, Focus::Card(file), false);
    }

    pub(super) fn event_anchor(&self, root: Point) -> Point {
        let page = self.page_size();
        let point = self.scroll.toplevel().and_then(|top| {
            let (_, x, y) = top.window()?.origin();
            let (l, t) = self.scroll.translate_coordinates(&top, 0, 0)?;
            Some((root.0 - f64::from(x + l), root.1 - f64::from(y + t)))
        });
        point
            .filter(|p| (0.0..=page.0).contains(&p.0) && (0.0..=page.1).contains(&p.1))
            .unwrap_or((page.0 * 0.5, page.1 * 0.5))
    }

    fn request_zoom(self: &Rc<Self>, zoom: f64, focus: Focus, gesture: bool) {
        if !zoom.is_finite() {
            return;
        }
        let zoom = zoom.clamp(0.15, 2.5);
        if self.zoom_animation.settling.get() || !self.zoom_animation.actions.borrow().is_empty() {
            self.zoom_animation.defer_zoom(zoom, focus, gesture);
            return;
        }
        self.cancel_key_pan();
        let ratio = zoom / self.zoom.get();
        let page = self.page_size();
        let (x, y) = self.viewport();
        let base = View {
            ratio: 1.0,
            vertical: 1.0,
            x,
            y,
        };
        let mut motion = self.zoom_animation.motion.borrow_mut();
        let from = motion.as_ref().map_or(base, |m| m.current);
        let point = match focus {
            Focus::Point(p) => p,
            _ => (page.0 * 0.5, page.1 * 0.5),
        };
        let world = from.board_point(point);
        let card_anchor = self.card_at(world).or_else(|| {
            // If the pivot is in a gap, anchor to the closest visible card.
            // Negative fractions represent the distance into that gap.
            let view = from.visible(page);
            let cards = self.cards.borrow();
            cards
                .iter()
                .filter(|card| {
                    let r = card.rect;
                    r.x < view.x + view.w
                        && r.x + r.w > view.x
                        && r.y < view.y + view.h
                        && r.y + r.h > view.y
                })
                .min_by(|a, b| {
                    let distance = |r: Rect| {
                        (r.x - world.0)
                            .max(world.0 - r.x - r.w)
                            .max(0.0)
                            .hypot((r.y - world.1).max(world.1 - r.y - r.h).max(0.0))
                    };
                    distance(a.rect).total_cmp(&distance(b.rect))
                })
                .map(|card| {
                    let r = card.rect;
                    (card.index, ((world.0 - r.x) / r.w, (world.1 - r.y) / r.h))
                })
        });
        let (x, y) = match focus {
            Focus::Point(_) => (world.0 * ratio - point.0, world.1 * ratio - point.1),
            Focus::Fit => (0.0, 0.0),
            Focus::Card(i) => {
                let cards = self.cards.borrow();
                let Some(card) = cards.iter().find(|c| c.index == i) else {
                    return;
                };
                let axis = |p: f64, s: f64, page: f64| {
                    if s * ratio > page {
                        p * ratio - 20.0
                    } else {
                        (p + s * 0.5) * ratio - page * 0.5
                    }
                };
                (
                    axis(card.rect.x, card.rect.w, page.0),
                    axis(card.rect.y, card.rect.h, page.1),
                )
            }
        };
        let (w, h) = self.board.borrow().size();
        let target = View {
            ratio,
            vertical: ratio,
            x: x.clamp(0.0, (w as f64 * ratio - page.0).max(0.0)),
            y: y.clamp(0.0, (h as f64 * ratio - page.1).max(0.0)),
        };
        let animate = self.animations_enabled() && self.scroll.is_mapped();
        if !animate && !gesture {
            drop(motion);
            self.cancel_zoom();
            self.zoom.set(zoom);
            self.render();
            // Apply the destination before GTK paints the new sizes. Its final
            // allocation can change scrollbar gutters, so reconcile afterwards.
            let place = move |c: &Rc<Self>| match focus {
                Focus::Card(i) => c.center_view(i),
                _ => c.pan_to((x, y)),
            };
            place(self);
            self.after_layout(place);
            return;
        }
        if motion.is_none() && (ratio - 1.0).abs() < 0.000001 {
            self.pan_to((target.x, target.y));
            return;
        }
        let now = glib::monotonic_time();
        let m = motion.get_or_insert_with(|| Transition {
            from,
            current: from,
            target,
            zoom,
            started: now,
            duration: 0.0,
            focus,
            card_anchor,
            picture: None,
            layout: None,
            ready: false,
            measure_pivot: true,
        });
        m.from = from;
        m.target = target;
        m.zoom = zoom;
        m.focus = focus;
        m.card_anchor = card_anchor;
        m.started = now;
        m.duration = if animate { EASE_US } else { 0.0 };
        m.layout = None;
        m.ready = false;
        m.measure_pivot = true;
        drop(motion);
        self.info
            .set_text(&format!("{}% · Zooming…", (zoom * 100.0).round() as i32));
        self.preview.show();
        self.preview.queue_draw();
        self.start_zoom_clock();
    }

    fn start_zoom_clock(self: &Rc<Self>) {
        if !self.zoom_animation.ticking.replace(true) {
            let weak = Rc::downgrade(self);
            self.scroll.add_tick_callback(move |_, _| {
                let Some(c) = weak.upgrade() else {
                    return glib::ControlFlow::Break;
                };
                if c.zoom_frame() {
                    glib::ControlFlow::Continue
                } else {
                    c.zoom_animation.ticking.set(false);
                    glib::ControlFlow::Break
                }
            });
        }
    }

    fn zoom_frame(self: &Rc<Self>) -> bool {
        let mut motion = self.zoom_animation.motion.borrow_mut();
        let Some(m) = motion.as_mut() else {
            return false;
        };
        if std::mem::take(&mut m.measure_pivot) {
            if let (Focus::Point(p), Some((index, _))) = (m.focus, m.card_anchor) {
                let old = self
                    .cards
                    .borrow()
                    .iter()
                    .find(|c| c.index == index)
                    .map(|c| c.rect);
                if let (Some(old), Some((w, h))) = (old, self.preview_card_size(index, m.zoom)) {
                    let world = m.from.board_point(p);
                    // Layout bounds were clamped at the requested map zoom.
                    // Font and control rounding only change the local picture
                    // scale; clamping again would slide a header off the pointer.
                    m.target = m.target.measured_about(world, w / old.w, h / old.h);
                }
            }
        }
        let elapsed = (glib::monotonic_time() - m.started).max(0) as f64;
        let t = if m.duration == 0.0 {
            1.0
        } else {
            (elapsed / m.duration).min(1.0)
        };
        m.current = m.from.towards(m.target, 1.0 - (1.0 - t).powi(2));
        self.preview.queue_draw();
        if m.ready {
            if t < 1.0 || self.zoom_animation.drag.get().is_some() {
                return true;
            }
            drop(motion);
            self.install_zoom_layout();
            return false;
        }
        if elapsed < 80_000.0 {
            return true;
        }
        if (m.zoom - self.zoom.get()).abs() < 0.000001 {
            let target = m.target;
            drop(motion);
            self.zoom_animation.motion.take();
            self.preview.hide();
            self.pan_to((target.x, target.y));
            self.flush_zoom_actions();
            return self.zoom_animation.motion.borrow().is_some();
        }
        if m.layout.is_none() {
            let board = gtk::Layout::new(gtk::Adjustment::NONE, gtk::Adjustment::NONE);
            self.bind_input(&board, None);
            m.layout = Some(render::Layout::new(self, board, m.zoom));
            let weak = Rc::downgrade(self);
            let started = m.started;
            glib::idle_add_local(move || {
                let Some(c) = weak.upgrade() else {
                    return glib::ControlFlow::Break;
                };
                let mut motion = c.zoom_animation.motion.borrow_mut();
                let Some(m) = motion.as_mut().filter(|m| m.started == started) else {
                    return glib::ControlFlow::Break;
                };
                let Some(layout) = m.layout.as_mut() else {
                    return glib::ControlFlow::Break;
                };
                let deadline = Instant::now() + Duration::from_millis(4);
                while layout.draw_next(&c).is_some() {
                    if Instant::now() >= deadline {
                        return glib::ControlFlow::Continue;
                    }
                }
                // GTK rounds fonts, borders and checkbox sizes independently.
                // Ease the landmark to its measured dimensions before replacing
                // its image with live text. This avoids a second jump at rest.
                m.ready = true;
                m.from = m.current;
                if let (Focus::Point(p), Some((index, fraction))) = (m.focus, m.card_anchor) {
                    let old = c
                        .cards
                        .borrow()
                        .iter()
                        .find(|c| c.index == index)
                        .map(|c| c.rect);
                    if let (Some(old), Some(new)) = (old, layout.rect(index)) {
                        let sx = new.w / old.w;
                        let sy = new.h / old.h;
                        let origin = (
                            new.x + fraction.0 * new.w - p.0,
                            new.y + fraction.1 * new.h - p.1,
                        );
                        let page = c.page_size();
                        let size = layout.board_size();
                        let x = origin.0.clamp(0.0, (size.0 as f64 - page.0).max(0.0));
                        let y = origin.1.clamp(0.0, (size.1 as f64 - page.1).max(0.0));
                        m.target = View {
                            ratio: sx,
                            vertical: sy,
                            x: old.x * sx - new.x + x,
                            y: old.y * sy - new.y + y,
                        };
                    }
                }
                m.started = glib::monotonic_time();
                let size = c.page_size();
                let a = m.from;
                let b = m.target;
                let mismatch = (a.x - b.x)
                    .abs()
                    .max((a.y - b.y).abs())
                    .max(size.0 * (a.ratio / b.ratio - 1.0).abs())
                    .max(size.1 * (a.vertical / b.vertical - 1.0).abs());
                m.duration = if mismatch > 1.0 && c.animations_enabled() {
                    75_000.0
                } else {
                    0.0
                };
                drop(motion);
                c.start_zoom_clock();
                glib::ControlFlow::Break
            });
        }
        // The settled picture stays on screen while idle slices prepare the
        // real text and controls. New input starts a fresh frame callback.
        t < 1.0
    }

    fn install_zoom_layout(self: &Rc<Self>) {
        let mut motion = self.zoom_animation.motion.borrow_mut();
        let mut finished = motion.take().unwrap();
        drop(motion);
        let mut layout = finished.layout.take().unwrap();
        let new_board = layout.board.clone();
        self.zoom.set(finished.zoom);
        layout.finish(self);
        let old_board = self.board.replace(new_board.clone());
        self.zoom_animation.handoff.replace(
            finished
                .picture
                .take()
                .map(|picture| LastFrame(picture, finished.current)),
        );
        self.zoom_animation.settling.set(true);
        self.scroll.remove(&old_board);
        self.scroll.add(&new_board);
        new_board.show_all();
        // Wait for GTK to settle scrollbar gutters and allocation before
        // applying the pivot and releasing any follow-up user input.
        let focus = finished.focus;
        let anchor = finished.card_anchor;
        let target = finished.target;
        let placed = Rc::new(Cell::new(false));
        let ready = placed.clone();
        let weak = Rc::downgrade(self);
        new_board.connect_draw(move |_, _| {
            if ready.replace(false) {
                let weak = weak.clone();
                glib::idle_add_local_once(move || {
                    if let Some(c) = weak
                        .upgrade()
                        .filter(|c| c.zoom_animation.settling.replace(false))
                    {
                        c.preview.hide();
                        c.zoom_animation.handoff.take();
                        c.flush_zoom_actions();
                    }
                });
            }
            glib::Propagation::Proceed
        });
        self.after_layout(move |c| {
            if !c.zoom_animation.settling.get() {
                return;
            }
            let point = if let (Focus::Point(p), Some((index, fraction))) = (focus, anchor) {
                c.cards
                    .borrow()
                    .iter()
                    .find(|card| card.index == index)
                    .map(|card| {
                        let r = card.rect;
                        (r.x + r.w * fraction.0 - p.0, r.y + r.h * fraction.1 - p.1)
                    })
            } else {
                None
            };
            if let Focus::Card(i) = focus {
                c.center_view(i);
            } else {
                c.pan_to(point.unwrap_or((target.x, target.y)));
            }
            placed.set(true);
            new_board.queue_draw();
        });
    }

    pub(super) fn zoom_in_flight(&self) -> bool {
        self.zoom_animation.settling.get() || self.zoom_animation.motion.borrow().is_some()
    }

    pub(super) fn after_zoom(self: &Rc<Self>, action: impl FnOnce(&Rc<Self>) + 'static) {
        if self.zoom_in_flight() {
            self.zoom_animation
                .actions
                .borrow_mut()
                .push(PendingInput::Action(Box::new(action)));
        } else {
            action(self);
        }
    }

    fn flush_zoom_actions(self: &Rc<Self>) {
        for input in self.zoom_animation.actions.take() {
            match input {
                PendingInput::Zoom {
                    zoom,
                    focus,
                    gesture,
                } => self.request_zoom(zoom, focus, gesture),
                PendingInput::Action(action) => self.after_zoom(action),
            }
        }
    }

    pub(super) fn cancel_zoom(&self) {
        self.zoom_animation.motion.take();
        self.zoom_animation.actions.take();
        self.zoom_animation.handoff.take();
        self.zoom_animation.settling.set(false);
        self.zoom_animation.drag.set(None);
        if self.preview.has_grab() {
            self.preview.grab_remove();
        }
        self.preview.hide();
    }
}

#[cfg(test)]
mod tests {
    use super::{Focus, PendingInput, View, ZoomAnimation};

    #[test]
    fn wheel_retargets_during_handoff_keep_only_the_last_destination_until_a_key() {
        let animation = ZoomAnimation::default();
        animation.defer_zoom(0.8, Focus::Fit, false);
        animation.defer_zoom(0.7, Focus::Point((30.0, 40.0)), true);
        animation
            .actions
            .borrow_mut()
            .push(PendingInput::Action(Box::new(|_| {})));
        animation.defer_zoom(0.9, Focus::Fit, false);
        assert!(matches!(
            animation.actions.borrow().as_slice(),
            [
                PendingInput::Zoom {
                    zoom: 0.7,
                    focus: Focus::Point((30.0, 40.0)),
                    gesture: true
                },
                PendingInput::Action(_),
                PendingInput::Zoom {
                    zoom: 0.9,
                    focus: Focus::Fit,
                    gesture: false
                },
            ]
        ));
    }
    #[test]
    fn measuring_a_short_header_keeps_its_point_under_the_pointer_near_the_top() {
        let view = View {
            ratio: 1.08,
            vertical: 1.08,
            x: 224.0,
            y: 28.0,
        };
        let pointer = (604.0, 354.0);
        let world = view.board_point(pointer);
        let measured = view.measured_about(world, 1.097, 0.939);
        let actual = measured.board_point(pointer);
        assert!((actual.0 - world.0).abs() < 1e-8);
        assert!((actual.1 - world.1).abs() < 1e-8);
    }
    #[test]
    fn intermediate_frames_keep_the_zoom_pivot_fixed() {
        let from = View {
            ratio: 1.0,
            vertical: 1.0,
            x: 120.0,
            y: 540.0,
        };
        let to = View {
            ratio: 1.5,
            vertical: 1.5,
            x: 330.0,
            y: 935.0,
        };
        let pivot = (300.0, 250.0);
        for step in 0..=10 {
            let world = from.towards(to, step as f64 / 10.0).board_point(pivot);
            assert!((world.0 - 420.0).abs() < 1e-9 && (world.1 - 790.0).abs() < 1e-9);
        }
    }
}
