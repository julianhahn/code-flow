use gtk::prelude::*;
use std::{collections::HashSet, sync::LazyLock};

const CSS: &str = include_str!("theme.css");
static CLASSES: LazyLock<HashSet<&'static str>> = LazyLock::new(|| {
    regex::Regex::new(r"\.([a-z][a-z0-9-]*)")
        .expect("valid CSS class expression")
        .captures_iter(concat!(
            include_str!("theme.css"),
            include_str!("appearance.css")
        ))
        .map(|c| c.get(1).unwrap().as_str())
        .collect()
});

fn check_class(name: &str) {
    assert!(CLASSES.contains(name), "Unknown theme class {name:?}");
}

pub fn style(widget: &impl IsA<gtk::Widget>, name: &str) {
    check_class(name);
    widget.style_context().add_class(name);
}

const DARK: &str = include_str!("dark.css");
const LIGHT: &str = include_str!("light.css");

/// One mutable screen provider also reaches popovers and secondary windows.
pub struct Appearance {
    provider: gtk::CssProvider,
    light: std::cell::Cell<bool>,
    review: bool,
}

impl Appearance {
    pub fn new(review: bool) -> std::rc::Rc<Self> {
        let appearance = std::rc::Rc::new(Self {
            provider: gtk::CssProvider::new(),
            light: false.into(),
            review,
        });
        let path = crate::backend::state_dir().join("appearance");
        let light = std::fs::read_to_string(path).is_ok_and(|s| s.trim() == "light");
        appearance.apply(light);
        gtk::StyleContext::add_provider_for_screen(
            &gdk::Screen::default().unwrap(),
            &appearance.provider,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
        appearance
    }

    pub fn light(&self) -> bool {
        self.light.get()
    }

    fn apply(&self, light: bool) {
        self.light.set(light);
        let palette = if light { LIGHT } else { DARK };
        // The standalone flow viewer uses native GTK controls and Cairo artwork.
        if self.review {
            self.provider
                .load_from_data(
                    format!("{palette}\n{CSS}\n{}", include_str!("appearance.css")).as_bytes(),
                )
                .expect("valid theme CSS");
        } else if let Some(settings) = gtk::Settings::default() {
            settings.set_gtk_application_prefer_dark_theme(!light);
            self.provider
                .load_from_data(include_bytes!("appearance.css"))
                .expect("valid appearance control CSS");
        }
    }

    pub fn toggle(self: &std::rc::Rc<Self>, changed: impl Fn() + 'static) -> gtk::CheckButton {
        let button = gtk::CheckButton::with_label("Light mode");
        button.set_widget_name("appearance-toggle");
        style(&button, "appearance");
        button.set_active(self.light());
        let appearance = self.clone();
        button.connect_toggled(move |b| {
            let light = b.is_active();
            appearance.apply(light);
            changed();
            let dir = crate::backend::state_dir();
            let save = || -> std::io::Result<()> {
                std::fs::create_dir_all(&dir)?;
                let mut file = tempfile::NamedTempFile::new_in(&dir)?;
                use std::io::Write;
                file.write_all(if light { b"light\n" } else { b"dark\n" })?;
                file.persist(dir.join("appearance"))?;
                Ok(())
            };
            if let Err(e) = save() {
                eprintln!("Could not save appearance: {e}");
            }
        });
        button
    }
}

pub(crate) fn provider(css: &str) -> gtk::CssProvider {
    let provider = gtk::CssProvider::new();
    provider
        .load_from_data(css.as_bytes())
        .expect("valid Code Flow widget CSS");
    provider
}

pub(crate) fn color(widget: &impl IsA<gtk::Widget>, token: &str) -> gdk::RGBA {
    widget
        .style_context()
        .lookup_color(&format!("cf_{token}"))
        .expect("theme installed before creating a diff map")
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    #[test]
    #[should_panic(expected = "Unknown theme class")]
    fn rejects_unknown_class() {
        super::check_class("focusd-card");
    }

    #[test]
    fn all_text_meets_wcag_aa_on_its_background() {
        for palette in [super::DARK, super::LIGHT] {
            let colors: HashMap<_, _> = palette
                .lines()
                .filter_map(|line| line.strip_prefix("@define-color cf_"))
                .map(|definition| {
                    let (token, hex) = definition.trim_end_matches(';').split_once(" #").unwrap();
                    let rgb = u32::from_str_radix(hex, 16).unwrap();
                    let linear = |shift: u32| {
                        let channel = f64::from((rgb >> shift) & 255) / 255.;
                        if channel <= 0.03928 {
                            channel / 12.92
                        } else {
                            ((channel + 0.055) / 1.055).powf(2.4)
                        }
                    };
                    (
                        token,
                        0.2126 * linear(16) + 0.7152 * linear(8) + 0.0722 * linear(0),
                    )
                })
                .collect();

            for (foreground, backgrounds) in [
                ("text", &["bg", "panel", "raised", "user_bg"][..]),
                ("text_dim", &["bg", "panel", "raised"]),
                ("muted", &["bg", "panel", "raised"]),
                ("accent", &["bg", "raised"]),
                ("on_accent", &["accent_solid"]),
                ("good", &["good_bg", "bg", "panel"]),
                ("warn", &["warn_bg", "panel"]),
                ("bad", &["bad_bg", "bg"]),
                ("add_fg", &["add_bg"]),
                ("del_fg", &["del_bg"]),
                ("hunk_fg", &["hunk_bg"]),
                ("hit_fg", &["hit_bg"]),
                ("current_fg", &["current_bg"]),
                ("node", &["panel"]),
            ] {
                for bg in backgrounds {
                    let (a, b) = (colors[foreground], colors[bg]);
                    let ratio = (a.max(b) + 0.05) / (a.min(b) + 0.05);
                    assert!(ratio >= 4.5, "{foreground} on {bg}: {ratio:.2}:1");
                }
            }
        }
    }
}
