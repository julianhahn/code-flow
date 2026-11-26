//! Work with immutable Git objects in a dedicated review clone.
use crate::canvas::FilePatch;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::env;
use std::ffi::OsStr;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

pub mod discussions;
pub mod pi;
pub type Result<T> = std::result::Result<T, String>;

fn nonempty_env(key: &str) -> Option<PathBuf> {
    env::var_os(key)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

pub fn home() -> PathBuf {
    nonempty_env("HOME")
        .or_else(std::env::home_dir)
        .unwrap_or_else(|| PathBuf::from("/"))
}

pub fn review_root() -> PathBuf {
    nonempty_env("CODE_FLOW_REVIEW_ROOT").unwrap_or_else(|| home().join("plancraft-review"))
}

const DEFAULT_REPOSITORY: &str = "plancraft/plancraft";

pub fn state_dir() -> PathBuf {
    let base = nonempty_env("XDG_STATE_HOME")
        .unwrap_or_else(|| home().join(".local/state"))
        .join("code-flow");
    repository_state_dir(base, &repo_name())
}

fn repository_state_dir(base: PathBuf, repo: &str) -> PathBuf {
    if repo == DEFAULT_REPOSITORY {
        base
    } else {
        let digest = Sha256::digest(repo.as_bytes());
        base.join("repos").join(format!("{digest:x}"))
    }
}

pub fn repo_name() -> String {
    env::var("CODE_FLOW_REPO")
        .ok()
        .map(|s| s.trim().to_ascii_lowercase())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_REPOSITORY.into())
}

pub fn pr_scope(number: u64) -> String {
    format!("{}:pr:{number}", repo_name())
}

fn read_output(mut pipe: impl Read) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    pipe.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    Ok(bytes)
}

// Drain both pipes concurrently: patch output and transport diagnostics can each
// exceed an OS pipe, especially when Git fetches missing partial-clone objects.
fn run(root: &Path, program: &str, args: &[&str], cancel: Option<&AtomicBool>) -> Result<Vec<u8>> {
    let cancelled = || cancel.is_some_and(|flag| flag.load(Ordering::Relaxed));
    if cancelled() {
        return Err("Review build cancelled.".into());
    }
    // Let Git finish an in-flight checkout/fetch before honouring cancellation.
    // Interrupting either with SIGKILL can leave an index lock or a partial checkout.
    let mutates_clone = program == "git" && matches!(args.first(), Some(&"fetch" | &"checkout"));
    let mut command = Command::new(program);
    for (name, _) in env::vars_os() {
        if name.to_string_lossy().starts_with("GIT_") {
            command.env_remove(name);
        }
    }
    command
        .current_dir(root)
        .args(args)
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_LITERAL_PATHSPECS", "1")
        .env("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
        .env("GH_PROMPT_DISABLED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|e| format!("Could not start {program}: {e}"))?;
    let out = child.stdout.take().unwrap();
    let err = child.stderr.take().unwrap();
    let stdout = thread::spawn(move || read_output(out));
    let stderr = thread::spawn(move || read_output(err));
    let deadline = Instant::now() + Duration::from_secs(60);
    let status = loop {
        if (!mutates_clone && cancelled()) || Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(if Instant::now() >= deadline {
                format!("{program} timed out.")
            } else {
                "Review build cancelled.".into()
            });
        }
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => thread::sleep(Duration::from_millis(3)),
            Err(e) => return Err(e.to_string()),
        }
    };
    let out = stdout
        .join()
        .map_err(|_| "Could not read process output")??;
    let err = stderr
        .join()
        .map_err(|_| "Could not read process diagnostics")??;
    if cancelled() {
        return Err("Review build cancelled.".into());
    }
    if !status.success() {
        let message = String::from_utf8_lossy(&err).trim().to_owned();
        return Err(if message.is_empty() {
            format!("{program} exited with {status}")
        } else {
            message
        });
    }
    Ok(out)
}

fn gh(root: &Path, args: &[&str], cancel: Option<&AtomicBool>) -> Result<Value> {
    let out = run(root, "gh", args, cancel)?;
    serde_json::from_slice(&out).map_err(|e| format!("GitHub returned invalid JSON: {e}"))
}

struct Clone<'a> {
    root: PathBuf,
    cancel: Option<&'a AtomicBool>,
}

impl<'a> Clone<'a> {
    fn open(root: &Path, cancel: Option<&'a AtomicBool>) -> Result<Self> {
        let root = root
            .canonicalize()
            .map_err(|e| format!("Review checkout does not exist: {e}"))?;
        for protected in std::iter::once(home().join("plancraft"))
            .chain(nonempty_env("CODE_FLOW_PROTECTED_ROOT"))
        {
            if root.starts_with(protected.canonicalize().unwrap_or(protected)) {
                return Err("The main checkout cannot be used for reviews".into());
            }
        }
        let repo = Self { root, cancel };
        let actual = repo.text(&["rev-parse", "--show-toplevel"])?;
        let dir = repo.text(&["rev-parse", "--absolute-git-dir"])?;
        if Path::new(&actual).canonicalize().ok().as_ref() != Some(&repo.root) {
            return Err("Review path must be the repository root".into());
        }
        if Path::new(&dir).canonicalize().ok() != Some(repo.root.join(".git")) {
            return Err("Review checkout must be a separate clone, not a worktree".into());
        }
        Ok(repo)
    }

    fn review_lock(&self, exclusive: bool) -> Result<fs::File> {
        let file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(self.root.join(".git/code-flow-review.lock"))
            .map_err(|e| e.to_string())?;
        let locked = if exclusive {
            fs2::FileExt::try_lock_exclusive(&file)
        } else {
            fs2::FileExt::try_lock_shared(&file)
        };
        locked
            .map_err(|_| "The review checkout is busy in another Code Flow window.".to_owned())?;
        Ok(file)
    }

    fn git(&self, args: &[&str]) -> Result<Vec<u8>> {
        run(&self.root, "git", args, self.cancel)
    }

    fn text(&self, args: &[&str]) -> Result<String> {
        Ok(String::from_utf8_lossy(&self.git(args)?)
            .trim_end_matches(['\r', '\n'])
            .into())
    }

    fn resolve(&self, name: &str) -> Result<String> {
        if name.is_empty() || name.starts_with('-') || name.contains('\0') {
            return Err("Invalid Git reference".into());
        }
        self.text(&[
            "rev-parse",
            "--verify",
            "--end-of-options",
            &format!("{name}^{{commit}}"),
        ])
    }

    fn clean(&self) -> Result<()> {
        for operation in [
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
            "sequencer",
            "BISECT_LOG",
        ] {
            if self.root.join(".git").join(operation).exists() {
                return Err(format!("An operation is in progress: {operation}"));
            }
        }
        if !self
            .git(&["status", "--porcelain=v1", "-z", "--untracked-files=all"])?
            .is_empty()
        {
            return Err("Review checkout has local changes".into());
        }
        Ok(())
    }
}

pub fn branches(root: &Path, fetch: bool) -> Result<(Vec<String>, String)> {
    let repo = Clone::open(root, None)?;
    if fetch {
        let _guard = repo.review_lock(true)?;
        repo.clean()?;
        repo.git(&["fetch", "--no-recurse-submodules", "origin"])?;
    }
    let names = repo.text(&[
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads",
        "refs/remotes/origin",
    ])?;
    let branches: Vec<String> = names
        .lines()
        .filter(|n| *n != "origin/HEAD" && *n != "origin")
        .map(String::from)
        .collect();
    // Local clones without a configured remote HEAD can still review branches.
    let base = repo
        .text(&["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
        .ok()
        .or_else(|| {
            branches
                .iter()
                .find(|n| n.as_str() == "main" || n.as_str() == "master")
                .cloned()
        })
        .or_else(|| branches.first().cloned())
        .unwrap_or_else(|| "HEAD".into());
    Ok((branches, base))
}

pub fn overview(root: &Path, branch: &str, number: Option<&str>) -> Result<Option<Value>> {
    let repo = repo_name();
    let id = match number {
        Some(s) if !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()) => s.to_owned(),
        Some(_) => return Err("Enter a PR number, then press Enter.".into()),
        None => {
            if branch.trim().is_empty() {
                return Err("Select a branch first.".into());
            }
            let prs = gh(
                root,
                &[
                    "pr",
                    "list",
                    "--head",
                    branch.strip_prefix("origin/").unwrap_or(branch),
                    "--state",
                    "all",
                    "--limit",
                    "100",
                    "--json",
                    "number,state,updatedAt",
                    "--repo",
                    &repo,
                ],
                None,
            )?;
            let newest = prs
                .as_array()
                .ok_or("Expected a PR list")?
                .iter()
                .max_by_key(|p| (p["state"] == "OPEN", p["updatedAt"].as_str().unwrap_or("")));
            match newest {
                Some(p) => p["number"].as_u64().ok_or("Missing PR number")?.to_string(),
                None => return Ok(None),
            }
        }
    };
    let fields = "number,title,body,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,reviewDecision,statusCheckRollup,changedFiles,additions,deletions";
    gh(
        root,
        &["pr", "view", &id, "--json", fields, "--repo", &repo],
        None,
    )
    .map(Some)
}

#[derive(Debug)]
pub struct Review {
    pub merge: String,
    pub head: String,
    pub files: Vec<FilePatch>,
    pub scope: String,
}

fn pr_snapshot(
    repo: &Clone<'_>,
    number: u64,
    metadata: &Value,
    current: &Value,
    report: impl Fn(&str),
) -> Result<(String, String, String)> {
    report("Fetching the reviewed commit…");
    repo.git(&[
        "fetch",
        "--no-recurse-submodules",
        "origin",
        &format!("refs/pull/{number}/head"),
    ])?;
    let head = repo.resolve("FETCH_HEAD")?;
    if current["headRefOid"] != head
        || metadata["headRefOid"] != head
        || current["baseRefName"] != metadata["baseRefName"]
    {
        return Err("PR changed. Select it again to reload its overview.".into());
    }
    let base_oid = current["baseRefOid"]
        .as_str()
        .filter(|s| matches!(s.len(), 40 | 64) && s.bytes().all(|b| b.is_ascii_hexdigit()))
        .ok_or("Missing PR base commit")?;
    report("Fetching the comparison base…");
    repo.git(&["fetch", "--no-recurse-submodules", "origin", base_oid])?;
    Ok((head, repo.resolve(base_oid)?, pr_scope(number)))
}

pub fn build(
    root: &Path,
    branch: &str,
    base: &str,
    pr: Option<&Value>,
    cancel: &AtomicBool,
    progress: impl Fn(usize, &str, f64),
) -> Result<Review> {
    progress(0, "Checking the separate review clone…", 0.);
    let repo = Clone::open(root, Some(cancel))?;
    let _guard = repo.review_lock(true)?;
    repo.clean()?;
    let (head, base_sha, scope) = if let Some(metadata) = pr {
        let number = metadata["number"]
            .as_u64()
            .ok_or("Enter a numeric PR number.")?;
        progress(0, &format!("Checking PR #{number}…"), 0.);
        let current = gh(
            root,
            &[
                "pr",
                "view",
                &number.to_string(),
                "--repo",
                &repo_name(),
                "--json",
                "baseRefName,baseRefOid,headRefOid",
            ],
            Some(cancel),
        )?;
        pr_snapshot(&repo, number, metadata, &current, |message| {
            progress(0, message, 0.)
        })?
    } else {
        (
            repo.resolve(branch)?,
            repo.resolve(base)?,
            format!("{}:branch:{branch}:base:{base}", repo.root.display()),
        )
    };
    progress(0, "Comparing commits…", 0.);
    let merge = repo.text(&["merge-base", &base_sha, &head])?;
    let raw = repo.git(&[
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--name-status",
        "-z",
        "--find-renames",
        &merge,
        &head,
        "--",
    ])?;
    let mut fields = raw.split(|&c| c == 0);
    let mut changed = Vec::new();
    while let Some(status) = fields.next().filter(|s| !s.is_empty()) {
        let field = |v: Option<&[u8]>| {
            v.map(|b| String::from_utf8_lossy(b).into_owned())
                .ok_or("Invalid Git name-status output")
        };
        let old_path = field(fields.next())?;
        let path = if matches!(status.first(), Some(b'R' | b'C')) {
            field(fields.next())?
        } else {
            old_path.clone()
        };
        changed.push((String::from_utf8_lossy(status).into_owned(), old_path, path));
    }
    progress(0, &format!("Checking out {}…", &head[..10]), 0.);
    repo.clean()?;
    repo.git(&["checkout", "--detach", "--no-overwrite-ignore", &head, "--"])?;
    let total = changed.len();
    let mut files = Vec::with_capacity(total);
    progress(1, &format!("Reading {total} changed files…"), 0.);
    for (index, (status, old_path, path)) in changed.into_iter().enumerate() {
        progress(
            1,
            &format!("Reading {}/{total}: {path}", index + 1),
            index as f64 / total as f64,
        );
        let mut args = vec![
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--find-renames",
            "--unified=3",
            &merge,
            &head,
            "--",
        ];
        if old_path != path {
            args.push(&old_path);
        }
        args.push(&path);
        let patch = String::from_utf8_lossy(&repo.git(&args)?).into_owned();
        let mut args = vec!["log", "-1", "--format=%H", &head, "--", &path];
        if old_path != path {
            args.push(&old_path);
        }
        let file_commit = Some(repo.text(&args)?);
        progress(
            1,
            &format!("Read {}/{total}: {path}", index + 1),
            (index + 1) as f64 / total as f64,
        );
        files.push(FilePatch {
            status,
            old_path,
            path,
            patch,
            file_commit,
        });
    }
    if cancel.load(Ordering::Relaxed) {
        return Err("Review build cancelled.".into());
    }
    progress(2, &format!("Preparing {total} file cards…"), 0.);
    Ok(Review {
        merge,
        head,
        files,
        scope,
    })
}

pub fn open_in_zed(root: &Path, path: &str, line: Option<u32>, column: Option<u32>) -> Result<()> {
    let root = root.canonicalize().map_err(|e| e.to_string())?;
    let file = root
        .join(path)
        .canonicalize()
        .ok()
        .filter(|f| f.starts_with(&root) && f.is_file())
        .ok_or_else(|| format!("File is not available in the review checkout: {path}"))?;
    let mut location = file.into_os_string();
    if let Some(line) = line {
        location.push(format!(":{}:{}", line.max(1), column.unwrap_or(1).max(1)));
    }
    let output = Command::new("zed")
        .arg(root)
        .arg(location)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| format!("Could not open Zed: {e}"))?;
    if !output.status.success() {
        let diagnostic = String::from_utf8_lossy(&output.stderr);
        let detail = diagnostic.trim();
        return Err(if detail.is_empty() {
            format!("Could not open Zed: {}", output.status)
        } else {
            format!("Could not open Zed ({}): {detail}", output.status)
        });
    }
    Ok(())
}

pub fn checks_summary(checks: &Value) -> String {
    let Some(items) = checks.as_array().filter(|a| !a.is_empty()) else {
        return "Checks: none reported".into();
    };
    let mut counts = [0; 4];
    for c in items {
        let s = ["conclusion", "state", "status"]
            .iter()
            .filter_map(|key| c[key].as_str())
            .find(|s| !s.is_empty())
            .unwrap_or("");
        match s {
            "FAILURE" | "ERROR" | "TIMED_OUT" | "CANCELLED" | "ACTION_REQUIRED" | "STALE"
            | "STARTUP_FAILURE" => counts[0] += 1,
            "QUEUED" | "IN_PROGRESS" | "PENDING" | "WAITING" | "REQUESTED" | "EXPECTED" => {
                counts[1] += 1
            }
            "SKIPPED" | "NEUTRAL" => counts[2] += 1,
            "SUCCESS" => (),
            _ => counts[3] += 1,
        }
    }
    let mut parts = vec![if counts[0] == 0 {
        "no failures".into()
    } else {
        format!("{} failed", counts[0])
    }];
    for (n, name) in counts[1..].iter().zip(["pending", "skipped", "unknown"]) {
        if *n > 0 {
            parts.push(format!("{n} {name}"));
        }
    }
    format!("Checks: {}", parts.join(" · "))
}

pub fn purpose_excerpt(body: &str) -> String {
    let para = regex::Regex::new(r"\n\s*\n").unwrap();
    let link = regex::Regex::new(r"\[([^\]]+)\]\([^)]+\)").unwrap();
    let normalized = body.replace("\r\n", "\n");
    for p in para.split(&normalized).map(str::trim) {
        if p.is_empty()
            || ["#", ">", "<", "---", "```", "|", "- [", "* ["]
                .iter()
                .any(|s| p.starts_with(s))
        {
            continue;
        }
        let plain = link.replace_all(p, "$1").replace(['`', '*', '_'], "");
        let text = plain.split_whitespace().collect::<Vec<_>>().join(" ");
        if text.chars().count() > 360 {
            let prefix = text.chars().take(357).collect::<String>();
            return format!(
                "{}…",
                prefix.rsplit_once(' ').map(|p| p.0).unwrap_or(&prefix)
            );
        }
        return text;
    }
    "No short description available. Read the full description on GitHub.".into()
}

pub(super) fn str_field<'a>(v: &'a Value, key: &str) -> &'a str {
    v[key].as_str().unwrap_or("")
}

pub(super) fn database(name: &str) -> Result<rusqlite::Connection> {
    let dir = state_dir();
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let db = rusqlite::Connection::open(dir.join(OsStr::new(name))).map_err(|e| e.to_string())?;
    db.busy_timeout(Duration::from_secs(3))
        .map_err(|e| e.to_string())?;
    Ok(db)
}

#[cfg(test)]
mod tests;
