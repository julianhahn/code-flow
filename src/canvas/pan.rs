//! Keyboard pans share a destination across key repeats. Each display frame
//! moves the native scroll adjustments, leaving the cards and selection intact.
use super::*;

const DURATION_US: i64 = 160_000;
const PAGE_FRACTION: f64 = 0.4;
type Point = [f64; 2];

#[derive(Clone, Copy)]
struct Motion {
    start_us: i64,
    from: Point,
    to: Point,
    // Derivative in pixels per normalized animation interval.
    tangent: Point,
}

impl Motion {
    fn new(start_us: i64, from: Point, to: Point, velocity: Point) -> Self {
        // A cubic with zero end velocity is monotone if the starting tangent
        // lies between zero and three times the distance. Drop the old
        // velocity on reversal so a fresh key never pushes past its target.
        let tangent = std::array::from_fn(|i| {
            let limit = 3.0 * (to[i] - from[i]);
            (velocity[i] * DURATION_US as f64).clamp(limit.min(0.0), limit.max(0.0))
        });
        Self {
            start_us,
            from,
            to,
            tangent,
        }
    }

    fn sample(&self, now_us: i64) -> (Point, Point, bool) {
        let t = ((now_us - self.start_us) as f64 / DURATION_US as f64).clamp(0.0, 1.0);
        if t >= 1.0 {
            return (self.to, [0.0; 2], true);
        }
        // Cubic Hermite interpolation with zero end velocity.
        let progress = t * t * (3.0 - 2.0 * t);
        let carry = t * (1.0 - t).powi(2);
        let position = std::array::from_fn(|i| {
            self.from[i] + (self.to[i] - self.from[i]) * progress + self.tangent[i] * carry
        });
        let velocity = std::array::from_fn(|i| {
            ((self.to[i] - self.from[i]) * 6.0 * t * (1.0 - t)
                + self.tangent[i] * (1.0 - t) * (1.0 - 3.0 * t))
                / DURATION_US as f64
        });
        (position, velocity, false)
    }
}

fn destination(base: Point, axis: usize, sign: f64, page: f64, lower: f64, upper: f64) -> Point {
    let mut to = base;
    to[axis] = (base[axis] + sign * PAGE_FRACTION * page).clamp(lower, (upper - page).max(lower));
    to
}

#[derive(Default)]
pub(super) struct KeyPan {
    motion: Cell<Option<Motion>>,
    tick: RefCell<Option<gtk::TickCallbackId>>,
    applying: Cell<bool>,
    generation: Cell<usize>,
}

impl Canvas {
    pub(super) fn animations_enabled(&self) -> bool {
        #[cfg(target_os = "macos")]
        if super::input::quartz::reduce_motion() {
            return false;
        }
        self.scroll
            .settings()
            .is_some_and(|s| s.is_gtk_enable_animations())
    }

    pub(super) fn cancel_key_pan(&self) {
        self.key_pan
            .generation
            .set(self.key_pan.generation.get().wrapping_add(1));
        self.stop_key_pan();
    }

    fn stop_key_pan(&self) {
        self.key_pan.motion.set(None);
        if let Some(tick) = self.key_pan.tick.take() {
            tick.remove();
        }
    }

    pub(super) fn bind_key_pan(self: &Rc<Self>) {
        self.scroll
            .connect_unmap(glib::clone!(@weak self as c => move |_| c.cancel_key_pan()));
        for adj in [self.scroll.hadjustment(), self.scroll.vadjustment()] {
            // A resize changes the step and bounds. Native scrollbar drags,
            // touchpad momentum, or another camera action take ownership of
            // the currently displayed position when they change its value.
            adj.connect_changed(glib::clone!(@weak self as c => move |_| c.stop_key_pan()));
            adj.connect_value_changed(glib::clone!(@weak self as c => move |_| {
                if !c.key_pan.applying.get() {
                    c.stop_key_pan();
                }
            }));
        }
    }

    pub fn scroll_by(self: &Rc<Self>, direction: &str) {
        let (axis, sign) = match direction {
            "up" => (1, -1.0),
            "down" => (1, 1.0),
            "left" => (0, -1.0),
            "right" => (0, 1.0),
            _ => return,
        };
        if self.camera_updates.borrow().is_empty() {
            self.start_key_pan(axis, sign);
        } else {
            // Viewed/zoom may already have scheduled a center against the new
            // geometry. A relative pan belongs after that center.
            let generation = self.key_pan.generation.get();
            self.after_layout(move |c| {
                if c.key_pan.generation.get() == generation {
                    c.start_key_pan(axis, sign);
                }
            });
        }
    }

    fn start_key_pan(self: &Rc<Self>, axis: usize, sign: f64) {
        if self.drag.get().is_some() {
            return;
        }
        let adjustments = [self.scroll.hadjustment(), self.scroll.vadjustment()];
        let from = [adjustments[0].value(), adjustments[1].value()];
        let a = &adjustments[axis];
        let old = self.key_pan.motion.get();
        let base = old.map_or(from, |motion| motion.to);
        let to = destination(base, axis, sign, a.page_size(), a.lower(), a.upper());
        if !self.animations_enabled() || !self.scroll.is_mapped() {
            self.stop_key_pan();
            adjustments[0].set_value(to[0]);
            adjustments[1].set_value(to[1]);
            return;
        }
        if to == base {
            return;
        }
        if to == from {
            self.stop_key_pan();
            return;
        }

        let now = glib::monotonic_time();
        let velocity = old.map_or([0.0; 2], |motion| motion.sample(now).1);
        self.key_pan
            .motion
            .set(Some(Motion::new(now, from, to, velocity)));
        if self.key_pan.tick.borrow().is_some() {
            return;
        }
        let Some(window) = self
            .scroll
            .toplevel()
            .and_then(|w| w.downcast::<gtk::Window>().ok())
        else {
            self.stop_key_pan();
            return;
        };
        let focus = window.focused_widget().map(|w| w.downgrade());
        let active = window.is_active();
        let window = window.downgrade();
        let weak = Rc::downgrade(self);
        let tick = self.scroll.add_tick_callback(move |scroll, clock| {
            let Some(c) = weak.upgrade() else {
                return glib::ControlFlow::Break;
            };
            let focused = window.upgrade().is_some_and(|w| {
                (!active || w.is_active())
                    && w.focused_widget() == focus.as_ref().and_then(|old| old.upgrade())
            });
            if let Some(motion) = c.key_pan.motion.get().filter(|_| focused) {
                let (point, _, done) = motion.sample(clock.frame_time());
                c.key_pan.applying.set(true);
                scroll.hadjustment().set_value(point[0]);
                scroll.vadjustment().set_value(point[1]);
                c.key_pan.applying.set(false);
                if !done {
                    return glib::ControlFlow::Continue;
                }
            }
            c.key_pan.motion.set(None);
            c.key_pan.tick.take();
            glib::ControlFlow::Break
        });
        self.key_pan.tick.replace(Some(tick));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_single_step_eases_into_motion_and_settles_exactly() {
        let m = Motion::new(10, [0., 10.], [400., 10.], [0.; 2]);
        let points: Vec<_> = (0..=10)
            .map(|n| m.sample(10 + DURATION_US * n / 10))
            .collect();
        assert_eq!(points[0], ([0., 10.], [0., 0.], false));
        assert_eq!(points[10], ([400., 10.], [0., 0.], true));
        assert!(points.windows(2).all(|p| p[0].0[0] < p[1].0[0]));
        assert!(points[1].0[0] < 20.);
        assert!(points[6].0[0] - points[5].0[0] > 40.);
        assert!(points.iter().all(|p| p.0[1] == 10. && p.1[1] == 0.));
    }

    #[test]
    fn repeats_continue_with_existing_speed_and_have_one_settling_interval() {
        let mut m = Motion::new(0, [0.; 2], [400., 0.], [0.; 2]);
        for n in 1..=8 {
            let now = 30_000 * n;
            let (point, velocity, _) = m.sample(now);
            let to = destination(m.to, 0, 1., 1000., 0., 10_000.);
            m = Motion::new(now, point, to, velocity);
            assert_eq!(m.sample(now).0, point);
            assert!((m.sample(now).1[0] - velocity[0]).abs() < 0.000_001);
        }
        assert_eq!(
            m.sample(8 * 30_000 + DURATION_US),
            ([3600., 0.], [0.; 2], true)
        );
    }

    #[test]
    fn reversal_and_mixed_axes_are_continuous_and_never_overshoot() {
        let m = Motion::new(0, [100., 0.], [500., 400.], [0.; 2]);
        let now = DURATION_US / 2;
        let (point, velocity, _) = m.sample(now);
        let m = Motion::new(now, point, [100., 400.], velocity);
        assert_eq!(m.sample(now).0, point);
        let mut previous = point;
        for t in 1..=12 {
            let (p, _, _) = m.sample(now + DURATION_US * t / 12);
            assert!(p[0] < previous[0] && p[0] >= 100.);
            assert!(p[1] > previous[1] && p[1] <= 400.);
            previous = p;
        }
        assert_eq!(previous, [100., 400.]);
    }

    #[test]
    fn capped_endpoints_drop_excess_steps_at_either_edge() {
        let next = |p, sign| destination(p, 0, sign, 200., 10., 510.);
        assert_eq!(next([300., 12.], 1.), [310., 12.]);
        assert_eq!(next([310., 12.], 1.), [310., 12.]);
        assert_eq!(next([310., 12.], -1.), [230., 12.]);
        assert_eq!(next([12., 30.], -1.), [10., 30.]);
        assert_eq!(destination([10., 0.], 1, 1., 500., 0., 400.), [10., 0.]);
    }
}
