use super::FilePatch;
use rusqlite::{params, Connection, OptionalExtension};
use sha2::{Digest, Sha256};
use std::{env, fmt::Write, path::PathBuf};

pub(super) struct Progress {
    db: Connection,
    scope: String,
    head: String,
}

// Existing rows were hashed by Python json.dumps with default separators and ensure_ascii.
fn fingerprint(file: &FilePatch) -> String {
    let mut json = String::from("[");
    for (i, value) in [&file.status, &file.old_path, &file.path, &file.patch]
        .into_iter()
        .enumerate()
    {
        if i != 0 {
            json.push_str(", ");
        }
        json.push('"');
        for c in value.chars() {
            match c {
                '"' => json.push_str("\\\""),
                '\\' => json.push_str("\\\\"),
                '\x08' => json.push_str("\\b"),
                '\x0c' => json.push_str("\\f"),
                '\n' => json.push_str("\\n"),
                '\r' => json.push_str("\\r"),
                '\t' => json.push_str("\\t"),
                ' '..='~' => json.push(c),
                _ => {
                    for unit in c.encode_utf16(&mut [0; 2]) {
                        write!(json, "\\u{unit:04x}").unwrap();
                    }
                }
            }
        }
        json.push('"');
    }
    json.push(']');
    format!("{:x}", Sha256::digest(json))
}

impl Progress {
    pub fn open(scope: &str, head: &str) -> Result<Self, String> {
        let mut db = if scope.is_empty() {
            Connection::open_in_memory()
        } else {
            let state = env::var_os("XDG_STATE_HOME")
                .filter(|s| !s.is_empty())
                .map(PathBuf::from)
                .or_else(|| env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/state")))
                .ok_or("No state directory available")?
                .join("code-flow");
            std::fs::create_dir_all(&state).map_err(|e| e.to_string())?;
            Connection::open(state.join("review-progress.sqlite3"))
        }
        .map_err(|e| e.to_string())?;
        db.busy_timeout(std::time::Duration::from_secs(5))
            .map_err(|e| e.to_string())?;
        Self::migrate(&mut db).map_err(|e| e.to_string())?;
        Ok(Self {
            db,
            scope: scope.into(),
            head: head.into(),
        })
    }

    fn migrate(db: &mut Connection) -> rusqlite::Result<()> {
        let tx = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute_batch("CREATE TABLE IF NOT EXISTS viewed (scope TEXT, path TEXT, fingerprint TEXT, reviewed_head TEXT, file_commit TEXT, PRIMARY KEY(scope,path))")?;
        let cols = tx
            .prepare("PRAGMA table_info(viewed)")?
            .query_map([], |r| r.get(1))?
            .collect::<rusqlite::Result<Vec<String>>>()?;
        for column in ["reviewed_head", "file_commit"] {
            if !cols.iter().any(|c| c == column) {
                tx.execute_batch(&format!("ALTER TABLE viewed ADD COLUMN {column} TEXT"))?;
            }
        }
        tx.commit()
    }

    pub fn viewed(&self, file: &FilePatch) -> rusqlite::Result<bool> {
        let row: Option<(String, Option<String>)> = self
            .db
            .query_row(
                "SELECT fingerprint, file_commit FROM viewed WHERE scope=?1 AND path=?2",
                params![self.scope, file.path],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        Ok(row.is_some_and(|(digest, commit)| {
            digest == fingerprint(file)
                && (self.head.is_empty()
                    || file.file_commit.is_some() && commit == file.file_commit)
        }))
    }

    pub fn set(&self, file: &FilePatch, value: bool) -> rusqlite::Result<()> {
        if value {
            self.db.execute("INSERT OR REPLACE INTO viewed(scope,path,fingerprint,reviewed_head,file_commit) VALUES(?,?,?,?,?)",
                params![self.scope,file.path,fingerprint(file),self.head,file.file_commit])?;
        } else {
            self.db.execute(
                "DELETE FROM viewed WHERE scope=? AND path=?",
                params![self.scope, file.path],
            )?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn legacy_fingerprint_and_commit() {
        let f = FilePatch {
            status: "M".into(),
            old_path: "ä".into(),
            path: "猫".into(),
            patch: "😀\n\t\u{7f}".into(),
            file_commit: Some("1".into()),
        };
        assert_eq!(
            fingerprint(&f),
            "bb5e80b66aaf6e460b804f946d72d0899221582456b5d1375671c4a0662ee51f"
        );
        let mut db = Connection::open_in_memory().unwrap();
        db.execute_batch(
            "CREATE TABLE viewed(scope TEXT,path TEXT,fingerprint TEXT,PRIMARY KEY(scope,path))",
        )
        .unwrap();
        Progress::migrate(&mut db).unwrap();
        let progress = Progress {
            db,
            scope: "pr".into(),
            head: "abc".into(),
        };
        progress.set(&f, true).unwrap();
        assert!(progress.viewed(&f).unwrap());
        assert!(!progress
            .viewed(&FilePatch {
                file_commit: Some("2".into()),
                ..f.clone()
            })
            .unwrap());
        progress.set(&f, false).unwrap();
        assert!(!progress.viewed(&f).unwrap());
    }
}
