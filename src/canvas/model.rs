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
    for line in raw.lines() {
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

impl Rect {
    pub fn score(self, other: Self, direction: &str) -> Option<(bool, i64, i64)> {
        let (start, size, orth, span, next, next_size, across, across_span) = match direction {
            "up" | "down" => (
                self.y, self.h, self.x, self.w, other.y, other.h, other.x, other.w,
            ),
            "right" | "left" => (
                self.x, self.w, self.y, self.h, other.x, other.w, other.y, other.h,
            ),
            _ => return None,
        };
        let gap = match direction {
            "down" | "right" if next > start => next - start - size,
            "up" | "left" if next < start => start - next - next_size,
            _ => return None,
        };
        let side = (across - orth - span)
            .max(orth - across - across_span)
            .max(0.0);
        Some((
            side > 0.0,
            ((gap.max(0.0) + 3.0 * side) * 1000.0).round() as i64,
            ((across - orth).abs() * 1000.0).round() as i64,
        ))
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
    #[test]
    fn neighbor_respects_card_edges() {
        let a = Rect {
            x: 0.0,
            y: 0.0,
            w: 100.0,
            h: 20.0,
        };
        let big = Rect {
            x: 0.0,
            y: 40.0,
            w: 1000.0,
            h: 500.0,
        };
        let below = Rect {
            x: 0.0,
            y: 560.0,
            w: 100.0,
            h: 20.0,
        };
        assert!(a.score(big, "down") < a.score(below, "down"));
        assert_eq!(a.score(big, "up"), None);
    }
}
