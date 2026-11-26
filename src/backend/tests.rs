use super::*;
use serde_json::json;
use tempfile::TempDir;

/// All Git operations use disposable directories and local-only repositories.
struct Fixture {
    temp: TempDir,
    source: PathBuf,
    remote: PathBuf,
    clone: PathBuf,
    initial: String,
}

fn git(root: &Path, args: &[&str]) -> String {
    let mut command = Command::new("git");
    for (name, _) in env::vars_os() {
        if name.to_string_lossy().starts_with("GIT_") {
            command.env_remove(name);
        }
    }
    let result = command
        .args([
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
        ])
        .current_dir(root)
        .args(args)
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "git {args:?}: {}",
        String::from_utf8_lossy(&result.stderr)
    );
    String::from_utf8_lossy(&result.stdout).trim_end().into()
}
impl Fixture {
    fn new() -> Self {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("source");
        let clone = temp.path().join("dedicated-review");
        let remote = temp.path().join("remote.git");
        git(
            temp.path(),
            &["init", "-q", "-b", "main", source.to_str().unwrap()],
        );
        fs::write(source.join("base"), "initial\n").unwrap();
        git(&source, &["add", "."]);
        git(&source, &["commit", "-qm", "Initial"]);
        let initial = git(&source, &["rev-parse", "HEAD"]);
        git(
            temp.path(),
            &[
                "clone",
                "-q",
                "--bare",
                source.to_str().unwrap(),
                remote.to_str().unwrap(),
            ],
        );
        git(
            &source,
            &["remote", "add", "origin", remote.to_str().unwrap()],
        );
        git(
            temp.path(),
            &[
                "clone",
                "-q",
                remote.to_str().unwrap(),
                clone.to_str().unwrap(),
            ],
        );
        Self {
            temp,
            source,
            remote,
            clone,
            initial,
        }
    }
    fn commit(&self, message: &str) -> String {
        git(&self.source, &["add", "--all"]);
        git(&self.source, &["commit", "-qm", message]);
        git(&self.source, &["rev-parse", "HEAD"])
    }
}

#[test]
fn branch_comparison_preserves_literal_paths_renames_and_file_commits() {
    let f = Fixture::new();
    git(&f.source, &["switch", "-qc", "topic"]);
    let name = "literal[abc] with space\nline.txt";
    fs::rename(f.source.join("base"), f.source.join(name)).unwrap();
    let touch = f.commit("Rename to unusual path");
    fs::write(f.source.join(":[special]"), b"\0\x01\x02").unwrap();
    let head = f.commit("Add binary with literal pathspec");
    git(&f.source, &["push", "-q", "origin", "topic"]);
    let (_, default) = branches(&f.clone, true).unwrap();
    assert_eq!(default, "origin/main");
    let review = build(
        &f.clone,
        "origin/topic",
        "origin/main",
        None,
        &AtomicBool::new(false),
        |_, _, _| {},
    )
    .unwrap();
    assert_eq!(review.merge, f.initial);
    assert_eq!(review.head, head);
    assert_eq!(review.files.len(), 2);
    let rename = review.files.iter().find(|x| x.path == name).unwrap();
    assert_eq!(rename.old_path, "base");
    assert!(rename.status.starts_with('R'));
    assert!(rename.patch.contains("rename from base"));
    assert_eq!(rename.file_commit.as_deref(), Some(touch.as_str()));
    let binary = review
        .files
        .iter()
        .find(|x| x.path == ":[special]")
        .unwrap();
    assert!(binary.patch.contains("Binary files"));
    assert_eq!(git(&f.clone, &["rev-parse", "HEAD"]), head);
    assert_eq!(git(&f.clone, &["branch", "--show-current"]), "");
    assert_eq!(git(&f.source, &["branch", "--show-current"]), "topic");
}

#[test]
fn unsafe_checkouts_invalid_refs_dirty_files_and_busy_operations_are_refused() {
    let f = Fixture::new();
    let repo = Clone::open(&f.clone, None).unwrap();
    for name in ["", "--option", "\0", "no-such-ref"] {
        assert!(repo.resolve(name).is_err());
    }
    fs::write(f.clone.join("untracked"), "user change").unwrap();
    assert!(build(
        &f.clone,
        "main",
        "main",
        None,
        &AtomicBool::new(false),
        |_, _, _| {}
    )
    .unwrap_err()
    .contains("local changes"));
    fs::remove_file(f.clone.join("untracked")).unwrap();
    fs::create_dir(f.clone.join(".git/rebase-merge")).unwrap();
    assert!(repo.clean().unwrap_err().contains("rebase-merge"));
    fs::remove_dir(f.clone.join(".git/rebase-merge")).unwrap();
    let tree = f.temp.path().join("linked");
    git(
        &f.clone,
        &[
            "worktree",
            "add",
            "--detach",
            tree.to_str().unwrap(),
            "HEAD",
        ],
    );
    assert!(Clone::open(&tree, None)
        .err()
        .unwrap()
        .contains("separate clone"));
    fs::create_dir(f.clone.join("sub")).unwrap();
    assert!(Clone::open(&f.clone.join("sub"), None)
        .err()
        .unwrap()
        .contains("root"));
    let _chat_lock = repo.review_lock(false).unwrap();
    assert!(repo
        .review_lock(true)
        .unwrap_err()
        .contains("another Code Flow"));
    let cancel = AtomicBool::new(true);
    assert!(build(&f.clone, "main", "main", None, &cancel, |_, _, _| {})
        .unwrap_err()
        .contains("cancelled"));
}

#[test]
fn pull_request_uses_pinned_base_even_when_clone_is_stale_and_branch_moves() {
    let f = Fixture::new();
    fs::write(f.source.join("shared"), "new base change").unwrap();
    let pinned = f.commit("Base change missing from stale clone");
    git(&f.source, &["push", "-q", "origin", "main"]);
    git(&f.source, &["switch", "-qc", "feature"]);
    fs::write(f.source.join("pr-file"), "only PR change").unwrap();
    let head = f.commit("Feature only");
    git(
        &f.source,
        &["push", "-q", "origin", "HEAD:refs/pull/7/head"],
    );
    // Before the GUI fetches, the named base moves onto the PR commit itself.
    git(&f.remote, &["update-ref", "refs/heads/main", &head]);
    assert_eq!(git(&f.clone, &["rev-parse", "origin/main"]), f.initial);
    let repo = Clone::open(&f.clone, None).unwrap();
    let metadata = json!({"number":7,"baseRefName":"main","headRefOid":head});
    let github = json!({"baseRefName":"main","baseRefOid":pinned,"headRefOid":head});
    let (selected, base, _) = pr_snapshot(&repo, 7, &metadata, &github, |_| {}).unwrap();
    assert_eq!(base, pinned);
    assert_eq!(selected, head);
    let review = build(
        &f.clone,
        &selected,
        &base,
        None,
        &AtomicBool::new(false),
        |_, _, _| {},
    )
    .unwrap();
    assert_eq!(
        review
            .files
            .iter()
            .map(|f| f.path.as_str())
            .collect::<Vec<_>>(),
        ["pr-file"]
    );
    let mut retargeted = github.clone();
    retargeted["baseRefName"] = json!("release");
    assert!(pr_snapshot(&repo, 7, &metadata, &retargeted, |_| {})
        .unwrap_err()
        .contains("PR changed"));
    let mut changed = github;
    changed["headRefOid"] = json!(f.initial);
    assert!(pr_snapshot(&repo, 7, &metadata, &changed, |_| {})
        .unwrap_err()
        .contains("PR changed"));
}

#[test]
fn overview_text_is_bounded_and_checks_preserve_unknowns() {
    assert_eq!(
        checks_summary(
            &json!([{"status":"IN_PROGRESS"},{"state":"unexpected"},{"conclusion":"SKIPPED"},{"conclusion":"CANCELLED"},{"conclusion":"SUCCESS"}])
        ),
        "Checks: 1 failed · 1 pending · 1 skipped · 1 unknown"
    );
    assert_eq!(checks_summary(&json!([])), "Checks: none reported");
    assert_eq!(purpose_excerpt("<!-- generated -->\r\n \r\n## Purpose\n\nLink [label](https://example.invalid) and _text_"),"Link label and text");
    assert!(purpose_excerpt(&"example ".repeat(65)).chars().count() <= 360);
}

#[test]
fn default_state_stays_in_place_and_pr_numbers_are_isolated_across_custom_repositories() {
    let base = PathBuf::from("/tmp/code-flow-state");
    assert_eq!(
        repository_state_dir(base.clone(), "plancraft/plancraft"),
        base
    );
    let first = repository_state_dir(base.clone(), "team/first");
    let second = repository_state_dir(base.clone(), "team/second");
    assert!(first.starts_with(base.join("repos")));
    assert_ne!(first, second);
    assert_eq!(first, repository_state_dir(base, "team/first"));
}
