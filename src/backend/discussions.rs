//! Read all GitHub discussion pages, including replies on resolved threads.
use super::{database, gh, repo_name, str_field, Result};
use rusqlite::params;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::path::Path;
use std::sync::atomic::AtomicBool;

const COMMENT: &str = "id body createdAt url author { login }";

fn graphql(root: &Path, query: &str, cancel: &AtomicBool) -> Result<Value> {
    let payload = gh(
        root,
        &["api", "graphql", "-f", &format!("query={query}")],
        Some(cancel),
    )?;
    if let Some(errors) = payload["errors"].as_array().filter(|a| !a.is_empty()) {
        return Err(errors
            .iter()
            .map(|e| str_field(e, "message"))
            .collect::<Vec<_>>()
            .join("; "));
    }
    Ok(payload["data"].clone())
}

fn connection(
    node: &str,
    ty: &str,
    field: &str,
    fields: &str,
    request: impl Fn(&str) -> Result<Value>,
) -> Result<Vec<Value>> {
    let mut cursor = None;
    let mut result = Vec::new();
    loop {
        let after = cursor
            .as_ref()
            .map(|c| format!(", after: {}", json!(c)))
            .unwrap_or_default();
        let query=format!("query {{ root: node(id:{}) {{ ... on {ty} {{ {field}(first:100{after}) {{ nodes {{ {fields} }} pageInfo {{ hasNextPage endCursor }} }} }} }} }}",json!(node));
        let data = request(&query)?;
        let page = &data["root"][field];
        result.extend(
            page["nodes"]
                .as_array()
                .ok_or("Missing GitHub conversation page")?
                .iter()
                .cloned(),
        );
        if page["pageInfo"]["hasNextPage"] != true {
            return Ok(result);
        }
        let next = page["pageInfo"]["endCursor"]
            .as_str()
            .ok_or("Missing GitHub page cursor")?
            .to_owned();
        if cursor.as_ref() == Some(&next) {
            return Err("GitHub did not advance the page cursor".into());
        }
        cursor = Some(next);
    }
}

pub struct Thread {
    pub id: String,
    pub title: String,
    pub comments: Vec<Value>,
}

pub fn load(root: &Path, number: u64, cancel: &AtomicBool) -> Result<Vec<Thread>> {
    let repo = repo_name();
    let (owner, name) = repo
        .split_once('/')
        .ok_or("Expected GitHub owner/repository")?;
    let query = format!(
        "query {{ repository(owner:{},name:{}) {{ pullRequest(number:{number}) {{ id }} }} }}",
        json!(owner),
        json!(name)
    );
    let data = graphql(root, &query, cancel)?;
    let id = data["repository"]["pullRequest"]["id"]
        .as_str()
        .ok_or("Pull request was not found")?;
    let mut items = Vec::new();
    for (field, title) in [("comments", "PR comment"), ("reviews", "Review")] {
        for c in connection(id, "PullRequest", field, COMMENT, |q| {
            graphql(root, q, cancel)
        })? {
            if !str_field(&c, "body").trim().is_empty() {
                items.push(Thread {
                    id: str_field(&c, "id").into(),
                    title: title.into(),
                    comments: vec![c],
                });
            }
        }
    }
    for thread in connection(
        id,
        "PullRequest",
        "reviewThreads",
        "id path line originalLine isResolved isOutdated",
        |q| graphql(root, q, cancel),
    )? {
        let id = str_field(&thread, "id").to_owned();
        let comments = connection(&id, "PullRequestReviewThread", "comments", COMMENT, |q| {
            graphql(root, q, cancel)
        })?;
        let mut title = str_field(&thread, "path").to_owned();
        if let Some(line) = thread["line"].as_u64().or(thread["originalLine"].as_u64()) {
            title.push_str(&format!(":{line}"));
        }
        if thread["isResolved"] == true {
            title.push_str(" · Resolved on GitHub");
        }
        if thread["isOutdated"] == true {
            title.push_str(" · Outdated");
        }
        items.push(Thread {
            id,
            title,
            comments,
        });
    }
    let time = |t: &Thread| {
        t.comments
            .first()
            .map(|c| str_field(c, "createdAt").to_owned())
            .unwrap_or_default()
    };
    items.sort_by_cached_key(time);
    Ok(items)
}

fn hidden_db() -> Result<rusqlite::Connection> {
    let db = database("conversations.sqlite3")?;
    db.execute_batch("CREATE TABLE IF NOT EXISTS hidden(pr INTEGER,id TEXT,PRIMARY KEY(pr,id))")
        .map_err(|e| e.to_string())?;
    Ok(db)
}

pub fn hidden(pr: u64) -> Result<HashSet<String>> {
    let db = hidden_db()?;
    let mut q = db
        .prepare("SELECT id FROM hidden WHERE pr=?")
        .map_err(|e| e.to_string())?;
    let ids = q.query_map([pr], |r| r.get(0)).map_err(|e| e.to_string())?;
    ids.collect::<rusqlite::Result<HashSet<_>>>()
        .map_err(|e| e.to_string())
}

pub fn set_hidden(pr: u64, id: &str, hide: bool) -> Result<()> {
    let db = hidden_db()?;
    let sql = if hide {
        "INSERT OR IGNORE INTO hidden(pr,id) VALUES(?,?)"
    } else {
        "DELETE FROM hidden WHERE pr=? AND id=?"
    };
    db.execute(sql, params![pr, id])
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn replies_are_paginated_at_their_own_node() {
        let pages = std::cell::Cell::new(0);
        let result=connection("PRT","PullRequestReviewThread","comments","body",|q|{
            assert!(q.contains("root: node(id:\"PRT\") { ... on PullRequestReviewThread"));
            let second=pages.replace(pages.get()+1)>0;
            assert_eq!(q.contains("after: \"continue\""),second);
            Ok(json!({"root":{"comments":{"nodes":[{"body":if second {"two"}else{"one"}}],"pageInfo":{"hasNextPage":!second,"endCursor":"continue"}}}}))
        }).unwrap();
        assert_eq!(result.len(), 2);
        assert_eq!(result[1]["body"], "two");
    }
}
