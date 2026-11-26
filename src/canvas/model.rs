use regex::Regex;
use std::sync::LazyLock;

pub const COLUMNS: [&str; 5] = ["DBTypes", "Frontend", "Shared", "Backend", "Other"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FilePatch {
    pub status: String,
    pub old_path: String,
    pub path: String,
    pub patch: String,
    pub file_commit: Option<String>,
}

pub fn group(path: &str) -> &'static str {
    let mut parts = path.split('/');
    if !matches!(parts.next(), Some("apps" | "libs" | "pkgs")) {
        return "Other";
    }
    let packages: Vec<_> = parts.take(2).collect();
    if packages
        .iter()
        .any(|p| matches!(*p, "dbtypes" | "db-types"))
    {
        return "DBTypes";
    }
    if packages.iter().any(|p| p.contains("-frontend")) {
        return "Frontend";
    }
    if packages.iter().any(|p| {
        p.contains("-backend")
            || ["-worker", "-jobs", "-agent", "-importer"]
                .iter()
                .any(|s| p.ends_with(s))
    }) {
        return "Backend";
    }
    if packages.iter().any(|p| p.ends_with("-shared")) {
        return "Shared";
    }
    if packages.iter().any(|p| {
        matches!(
            *p,
            "app"
                | "browser"
                | "mobile"
                | "public-pages"
                | "share-page"
                | "storybook"
                | "support-center"
                | "website-widgets"
        )
    }) {
        return "Frontend";
    }
    if packages
        .iter()
        .any(|p| matches!(*p, "api" | "cli" | "firestore-triggers"))
    {
        return "Backend";
    }
    "Other"
}

pub fn is_spec(path: &str) -> bool {
    static SPEC: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[._-](spec|test)\.").unwrap());
    let lower = path.to_lowercase();
    let mut parts = lower.rsplit('/');
    let name = parts.next().unwrap_or_default();
    name.starts_with("test_")
        || SPEC.is_match(name)
        || parts.any(|p| matches!(p, "spec" | "specs" | "__tests__" | "__snapshots__"))
}

pub struct PatchText {
    pub content: String,
    /// Tag name and UTF-8 character range. GTK text iter offsets count characters.
    pub ranges: Vec<(&'static str, i32, i32)>,
}

pub fn patch_text(raw: &str) -> PatchText {
    static HUNK: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@").unwrap());
    let mut numbers = None;
    let mut content = String::new();
    let mut ranges = Vec::new();
    let mut offset = 0i32;
    let display = crate::widgets::display_text(raw);
    for line in display.lines() {
        if let Some(captures) = HUNK.captures(line) {
            numbers = captures[1]
                .parse::<u64>()
                .ok()
                .zip(captures[2].parse::<u64>().ok());
            continue;
        }
        let kind = match (numbers.as_mut(), line.as_bytes().first()) {
            (Some((old, new)), Some(&prefix @ (b'+' | b'-' | b' '))) => {
                let left = if prefix == b'+' {
                    String::new()
                } else {
                    let n = old.to_string();
                    *old = old.saturating_add(1);
                    n
                };
                let right = if prefix == b'-' {
                    String::new()
                } else {
                    let n = new.to_string();
                    *new = new.saturating_add(1);
                    n
                };
                content.push_str(&format!("{left:>5} {right:>5}  "));
                offset += (left.len().max(5) + right.len().max(5) + 3) as i32;
                match prefix {
                    b'+' => "add",
                    b'-' => "del",
                    _ => "context",
                }
            }
            (Some(_), _) => "context",
            _ if [
                "Binary files",
                "GIT binary",
                "rename ",
                "similarity ",
                "old mode",
                "new mode",
            ]
            .iter()
            .any(|s| line.starts_with(s)) =>
            {
                "context"
            }
            _ => continue,
        };
        content.push_str(line);
        content.push('\n');
        offset += line.chars().count() as i32 + 1;
        // Consecutive lines of one kind share a single TextTag range.
        match ranges.last_mut() {
            Some((previous, _, end)) if *previous == kind => *end = offset,
            _ => {
                let start = ranges.last().map_or(0, |&(_, _, end)| end);
                ranges.push((kind, start, offset));
            }
        }
    }
    if let Some((_, _, end)) = ranges.last_mut() {
        content.pop();
        *end -= 1;
    } else {
        content.push_str("No text hunks (binary, rename, mode change, or empty file).");
        ranges.push(("context", 0, content.len() as i32));
    }
    PatchText { content, ranges }
}

#[derive(Clone, Copy, Debug)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

/// A card's vertical extent in the unzoomed folder layout. Slots are ordered
/// by column, then by path. Camera offsets and drawn pixel bounds never enter here.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct Slot {
    pub file: usize,
    pub column: usize,
    pub top: i64,
    pub bottom: i64,
}

#[derive(Default)]
pub(super) struct Navigation {
    slots: Vec<Slot>,
    anchor: Option<i64>,
}

impl Navigation {
    pub fn set_layout(&mut self, slots: Vec<Slot>) {
        if self.slots != slots {
            self.slots = slots;
            self.reset_anchor();
        }
    }

    pub fn reset_anchor(&mut self) {
        self.anchor = None;
    }

    pub fn step(&mut self, file: usize, direction: &str) -> Option<usize> {
        let index = self.slots.iter().position(|slot| slot.file == file)?;
        let current = self.slots[index];
        if direction == "up" || direction == "down" {
            let row = if direction == "up" {
                index.checked_sub(1)?
            } else {
                index + 1
            };
            let next = self.slots.get(row).filter(|s| s.column == current.column)?;
            self.anchor = None;
            return Some(next.file);
        }

        let column = match direction {
            "left" => self.slots[..index]
                .iter()
                .rev()
                .find(|s| s.column < current.column),
            "right" => self.slots[index + 1..]
                .iter()
                .find(|s| s.column > current.column),
            _ => None,
        }?
        .column;
        // Preserve the starting row across a tall card, or a short column, so
        // Left/Right can retrace a route without drifting up to that card's top.
        let y = self.anchor.unwrap_or(current.top);
        let next = self
            .slots
            .iter()
            .filter(|s| s.column == column)
            .min_by_key(|s| {
                let edge_distance = (s.top - y).max(y - s.bottom).max(0);
                (edge_distance, s.top.abs_diff(y))
            })?;
        self.anchor = Some(y);
        Some(next.file)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn grouping_and_specs() {
        assert_eq!(
            group("pkgs/support-center/support-center-backend-shared/x"),
            "Backend"
        );
        assert_eq!(group("pkgs/chat/chat-frontend-web/x"), "Frontend");
        assert_eq!(group("pkgs/demo-shared/x"), "Shared");
        assert_eq!(group("apps/app/x"), "Frontend");
        assert_eq!(group("apps/api/x"), "Backend");
        assert_eq!(group("pkgs/db-types/x"), "DBTypes");
        for p in [
            "A/B.SPEC.ts",
            "dir/test_thing.py",
            "a/__snapshots__/b",
            "a/my-test.go",
        ] {
            assert!(is_spec(p));
        }
        assert!(!is_spec("a/specification.rs"));
    }
    #[test]
    fn nul_in_a_text_diff_keeps_search_and_tag_offsets_without_changing_the_raw_patch() {
        let file = FilePatch {
            status: "M".into(),
            old_path: "text.dat".into(),
            path: "text.dat".into(),
            patch: "@@ -1 +1 @@\n-old\0猫\n+new\0😀\n".into(),
            file_commit: None,
        };
        let displayed = patch_text(&file.patch);
        assert!(file.patch.contains('\0'));
        assert!(!displayed.content.contains('\0'));
        assert!(displayed.content.ends_with("+new�😀"));
        let chars: Vec<_> = displayed.content.chars().collect();
        let (kind, start, end) = displayed.ranges[1];
        assert_eq!(kind, "add");
        assert_eq!(end as usize, chars.len());
        assert!(chars[start as usize..end as usize]
            .iter()
            .collect::<String>()
            .ends_with("+new�😀"));
    }

    #[test]
    fn numbered_unicode_patch_and_metadata() {
        let patch = patch_text(
            "--- a/x\n+++ b/x\n@@ -42,2 +8,2 @@ function\n-ä\n+😀\n same\n@@ -90 +1 @@\n+猫",
        );
        assert_eq!(
            patch.content,
            "   42        -ä\n          8  +😀\n   43     9   same\n          1  +猫"
        );
        assert_eq!(
            patch.ranges.last().unwrap().2,
            patch.content.chars().count() as i32
        );
        assert_eq!(patch.ranges.len(), 4);
        assert!(patch_text("old mode 100644")
            .content
            .starts_with("old mode"));
    }
    fn map(rows: &[(usize, i64, i64)]) -> Vec<Slot> {
        rows.iter()
            .enumerate()
            .map(|(file, &(column, top, bottom))| Slot {
                file,
                column,
                top,
                bottom,
            })
            .collect()
    }

    #[test]
    fn lateral_navigation_only_enters_the_adjacent_populated_column() {
        let slots = map(&[(0, 40, 60), (0, 120, 160), (2, 300, 380), (4, 40, 900)]);
        let mut nav = Navigation::default();
        nav.set_layout(slots);
        assert_eq!(nav.step(0, "right"), Some(2));
        assert_eq!(nav.step(2, "right"), Some(3));
        assert_eq!(nav.step(3, "right"), None);
        assert_eq!(nav.step(0, "left"), None);
        assert_eq!(nav.step(1, "down"), None);
        assert_eq!(nav.step(0, "up"), None);
        assert_eq!(nav.step(0, "down"), Some(1));
        assert_eq!(nav.step(1, "up"), Some(0));
    }

    #[test]
    fn horizontal_moves_retrace_the_row_through_tall_cards_and_zoom() {
        let slots = map(&[
            (0, 20, 80),
            (0, 100, 160),
            (0, 400, 550),
            (1, 1, 1000),
            (3, 40, 80),
            (3, 340, 390),
            (3, 401, 470),
        ]);
        let mut nav = Navigation::default();
        nav.set_layout(slots.clone());
        assert_eq!(nav.step(2, "right"), Some(3));
        // Zooming and panning leave the logical layout and anchor untouched.
        nav.set_layout(slots);
        assert_eq!(nav.step(3, "right"), Some(6));
        assert_eq!(nav.step(6, "left"), Some(3));
        assert_eq!(nav.step(3, "left"), Some(2));
        assert_eq!(nav.step(2, "up"), Some(1));
        assert_eq!(nav.step(1, "right"), Some(3));
        assert_eq!(nav.step(3, "right"), Some(4));
    }

    #[test]
    fn explicit_selection_and_changed_layout_reset_the_horizontal_anchor() {
        let slots = map(&[(0, 20, 60), (1, 0, 450), (2, 10, 25), (2, 100, 450)]);
        let mut nav = Navigation::default();
        nav.set_layout(slots.clone());
        assert_eq!(nav.step(3, "left"), Some(1));
        nav.reset_anchor(); // click or Files/search jump onto the tall card
        assert_eq!(nav.step(1, "right"), Some(2));
        nav.reset_anchor();
        assert_eq!(nav.step(3, "left"), Some(1));
        let mut collapsed = slots;
        collapsed[1].bottom = 30;
        nav.set_layout(collapsed);
        assert_eq!(nav.step(1, "right"), Some(2));
    }
}
