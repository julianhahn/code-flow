//! Paint and interact with the visible portion of the map during zoom.
use super::*;
use gdk::prelude::GdkContextExt;

pub(super) struct Picture {
    image: cairo::ImageSurface,
    area: Rect,
}

impl Picture {
    fn covers(&self, r: Rect) -> bool {
        r.x >= self.area.x
            && r.y >= self.area.y
            && r.x + r.w <= self.area.x + self.area.w
            && r.y + r.h <= self.area.y + self.area.h
    }

    fn capture(c: &Canvas, from: Rect, to: Rect, page: Point) -> Option<Self> {
        let x = from.x.min(to.x);
        let y = from.y.min(to.y);
        let w = (from.x + from.w).max(to.x + to.w) - x;
        let h = (from.y + from.h).max(to.y + to.h) - y;
        // Leave enough surrounding content for the next few wheel events. Limit
        // backing pixels even on Retina and on maps tens of thousands of points tall.
        let area = Rect {
            x: x - w * 0.12,
            y: y - h * 0.12,
            w: w * 1.24,
            h: h * 1.24,
        };
        let density = (c.scroll.scale_factor() as f64)
            .min(1.5 * page.0 / area.w * c.scroll.scale_factor() as f64)
            .min(1.5 * page.1 / area.h * c.scroll.scale_factor() as f64)
            .min((4_000_000.0 / (area.w * area.h)).sqrt());
        let image = cairo::ImageSurface::create(
            cairo::Format::Rgb24,
            (area.w * density).ceil() as i32,
            (area.h * density).ceil() as i32,
        )
        .ok()?;
        image.set_device_scale(density, density);
        let cr = cairo::Context::new(&image).ok()?;
        GdkContextExt::set_source_rgba(&cr, &crate::theme::color(&*c.board.borrow(), "bg"));
        cr.paint().ok()?;
        cr.translate(-area.x, -area.y);
        let board = c.board.borrow();
        for child in board.children() {
            let a = child.allocation();
            let (left, top) = (board.child_x(&child) as f64, board.child_y(&child) as f64);
            let (width, height) = (a.width() as f64, a.height() as f64);
            if left >= area.x + area.w
                || top >= area.y + area.h
                || left + width <= area.x
                || top + height <= area.y
                || !child.is_drawable()
            {
                continue;
            }
            cr.save().ok()?;
            cr.translate(left, top);
            cr.rectangle(0.0, 0.0, width, height);
            cr.clip();
            child.draw(&cr);
            cr.restore().ok()?;
        }
        Some(Self { image, area })
    }
}

pub(super) struct LastFrame(pub(super) Picture, pub(super) View);

impl Picture {
    fn paint(&self, cr: &cairo::Context, view: View) {
        cr.translate(-view.x, -view.y);
        cr.scale(view.ratio, view.vertical);
        if cr
            .set_source_surface(&self.image, self.area.x, self.area.y)
            .is_ok()
        {
            let _ = cr.paint();
        }
    }
}

impl Canvas {
    // The preview may still be visible when the user starts their next action.
    // Hit-test the artwork they actually clicked, using the original GTK bounds.
    fn preview_click(self: &Rc<Self>, point: Point, double: bool) {
        let (x, y) = self.viewport();
        let view = self
            .zoom_animation
            .motion
            .borrow()
            .as_ref()
            .map(|m| m.current)
            .unwrap_or(View {
                ratio: 1.0,
                vertical: 1.0,
                x,
                y,
            });
        let world = view.board_point(point);
        let Some((file, _)) = self.card_at(world) else {
            return;
        };
        let control = self
            .cards
            .borrow()
            .iter()
            .find(|c| c.index == file)
            .and_then(|card| {
                let header = card
                    .frame
                    .children()
                    .into_iter()
                    .next()?
                    .downcast::<gtk::Box>()
                    .ok()?;
                header
                    .children()
                    .into_iter()
                    .filter(|w| w.is::<gtk::Button>())
                    .find(|w| {
                        let Some((left, top)) = w.translate_coordinates(&card.frame, 0, 0) else {
                            return false;
                        };
                        let x = world.0 - card.rect.x - left as f64;
                        let y = world.1 - card.rect.y - top as f64;
                        x >= 0.0
                            && y >= 0.0
                            && x < w.allocated_width() as f64
                            && y < w.allocated_height() as f64
                    })
            });
        if let Some(control) = control {
            if !double {
                if control.is::<gtk::CheckButton>() {
                    self.after_zoom(move |c| {
                        let mark = !c.viewed.borrow().contains(&file);
                        c.set_viewed(file, mark);
                    });
                } else {
                    gtk::Clipboard::get(&gdk::SELECTION_CLIPBOARD).set_text(&self.files[file].path);
                }
            }
        } else if double {
            self.after_zoom(move |c| c.center_on_file(&c.files[file].path, 1.0));
        } else {
            self.after_zoom(move |c| c.select_file(file));
        }
    }

    fn pan_preview(self: &Rc<Self>, dx: f64, dy: f64) {
        let page = self.page_size();
        let (w, h) = self.board.borrow().size();
        let mut motion = self.zoom_animation.motion.borrow_mut();
        if let Some(m) = motion.as_mut() {
            let shift = |v: &mut View| {
                v.x = (v.x + dx).clamp(0.0, (w as f64 * v.ratio - page.0).max(0.0));
                v.y = (v.y + dy).clamp(0.0, (h as f64 * v.vertical - page.1).max(0.0));
            };
            shift(&mut m.from);
            shift(&mut m.current);
            shift(&mut m.target);
            let p = (page.0 * 0.5, page.1 * 0.5);
            m.focus = Focus::Point(p);
            m.card_anchor = self.card_at(m.target.board_point(p));
            self.preview.queue_draw();
        } else {
            drop(motion);
            self.after_zoom(move |c| {
                let (x, y) = c.viewport();
                c.pan_to((x + dx, y + dy));
            });
        }
    }

    pub(in crate::canvas) fn bind_zoom_preview(self: &Rc<Self>) {
        let weak = Rc::downgrade(self);
        self.preview.connect_draw(move |_, cr| {
            let Some(c) = weak.upgrade() else {
                return glib::Propagation::Proceed;
            };
            let page = c.page_size();
            let mut motion = c.zoom_animation.motion.borrow_mut();
            if let Some(m) = motion.as_mut() {
                let visible = m.current.visible(page);
                if m.picture.as_ref().is_none_or(|p| !p.covers(visible)) {
                    m.picture = Picture::capture(&c, visible, m.target.visible(page), page);
                }
                if let Some(picture) = &m.picture {
                    picture.paint(cr, m.current);
                }
            } else if let Some(LastFrame(picture, view)) =
                c.zoom_animation.handoff.borrow().as_ref()
            {
                picture.paint(cr, *view);
            }
            glib::Propagation::Stop
        });
        let weak = Rc::downgrade(self);
        self.preview
            .connect_button_press_event(move |widget, event| {
                let Some(c) = weak.upgrade() else {
                    return glib::Propagation::Proceed;
                };
                match event.button() {
                    1 => c.preview_click(
                        c.event_anchor(event.root()),
                        event.event_type() == gdk::EventType::DoubleButtonPress,
                    ),
                    2 => {
                        c.zoom_animation.drag.set(Some(event.root()));
                        widget.grab_add();
                    }
                    _ => return glib::Propagation::Proceed,
                }
                glib::Propagation::Stop
            });
        let weak = Rc::downgrade(self);
        self.preview.connect_motion_notify_event(move |_, event| {
            let Some(c) = weak.upgrade() else {
                return glib::Propagation::Proceed;
            };
            let Some(previous) = c.zoom_animation.drag.get() else {
                return glib::Propagation::Proceed;
            };
            let point = event.root();
            c.zoom_animation.drag.set(Some(point));
            c.pan_preview(previous.0 - point.0, previous.1 - point.1);
            glib::Propagation::Stop
        });
        let weak = Rc::downgrade(self);
        self.preview
            .connect_button_release_event(move |widget, event| {
                let Some(c) = weak.upgrade() else {
                    return glib::Propagation::Proceed;
                };
                if event.button() == 2 && c.zoom_animation.drag.take().is_some() {
                    if widget.has_grab() {
                        widget.grab_remove();
                    }
                    glib::Propagation::Stop
                } else {
                    glib::Propagation::Proceed
                }
            });
        let weak = Rc::downgrade(self);
        self.preview.connect_scroll_event(move |_, event| {
            let Some(c) = weak.upgrade() else {
                return glib::Propagation::Proceed;
            };
            let command = gdk::ModifierType::CONTROL_MASK
                | gdk::ModifierType::META_MASK
                | gdk::ModifierType::SUPER_MASK;
            if event.state().intersects(command) {
                return glib::Propagation::Proceed;
            }
            let delta = match event.direction() {
                gdk::ScrollDirection::Smooth => event.delta(),
                gdk::ScrollDirection::Up => (0.0, -1.0),
                gdk::ScrollDirection::Down => (0.0, 1.0),
                gdk::ScrollDirection::Left => (-1.0, 0.0),
                gdk::ScrollDirection::Right => (1.0, 0.0),
                _ => return glib::Propagation::Proceed,
            };
            let (width, height) = c.page_size();
            c.pan_preview(
                delta.0 * width.powf(2.0 / 3.0),
                delta.1 * height.powf(2.0 / 3.0),
            );
            glib::Propagation::Stop
        });
        self.bind_input(&self.preview, None);
        self.scroll
            .connect_unmap(glib::clone!(@weak self as c => move |_| c.cancel_zoom()));
    }
}
