use gtk::prelude::*;

/// Application-wide palette and widget styles, including auxiliary windows.
pub fn apply() {
    let provider = gtk::CssProvider::new();
    provider
        .load_from_data(include_bytes!("theme.css"))
        .expect("valid Code Flow CSS");
    if let Some(screen) = gdk::Screen::default() {
        gtk::StyleContext::add_provider_for_screen(
            &screen,
            &provider,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
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
    fn all_text_meets_wcag_aa_on_its_background() {
        let colors: HashMap<_, _> = include_str!("theme.css")
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
