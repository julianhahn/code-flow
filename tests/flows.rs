//! Exercise both wire formats through the documented headless executable.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    os::unix::fs::symlink,
    process::{Command, Output},
};

fn run(root: &std::path::Path, input: &Value) -> Output {
    let path = root.join("flow.json");
    fs::write(&path, serde_json::to_vec(input).unwrap()).unwrap();
    Command::new(env!("CARGO_BIN_EXE_code-flow"))
        .arg("--validate-flow")
        .arg(path)
        .arg("--root")
        .arg(root)
        .env_remove("DISPLAY")
        .output()
        .unwrap()
}

#[test]
fn compact_and_explicit_records_have_identical_positions_and_provenance() {
    let folder = tempfile::tempdir().unwrap();
    let source = b"first\nsecond\n";
    fs::write(folder.path().join("review.ts"), source).unwrap();
    let file = json!({"file":"review.ts","sha256":format!("{:x}",Sha256::digest(source))});
    let mut one = file.clone();
    one["line"] = json!(1);
    let mut two = file.clone();
    two["line"] = json!(2);
    let regions = json!([{"id":"main","label":"API"},{"id":"worker","label":"Worker"}]);
    let types = json!({"queue":{"relation":"handoff"}});
    let mut explicit = json!({"version":1,"title":"Publish","regions":regions,"edgeTypes":types,
        "nodes":[{"id":"a","kind":"step","title":"Submit","region":"main","source":one},
                 {"id":"b","kind":"return","title":"Return","region":"worker","source":two}],
        "edges":[{"from":"a","to":"b","type":"queue","origin":"agent","evidence":[one,two]}]});
    let mut compact = json!({"version":2,"title":"Publish","regions":regions,"edgeTypes":types,
        "sources":{"api":file},"defaults":{"source":"api","origin":"agent","edgeType":"queue","evidence":"endpoint-sources"},
        "nodeFields":["id","kind","title","region","line"],"nodes":[["b","return","Return","worker",2],["a","step","Submit","main",1]],
        "edgeFields":["from","to"],"edges":[["a","b"]]});
    let checked = run(folder.path(), &explicit);
    let packed = run(folder.path(), &compact);
    assert!(checked.status.success() && packed.status.success());
    assert_eq!(checked.stdout, packed.stdout);
    let parsed: Value = serde_json::from_slice(&packed.stdout).unwrap();
    assert_eq!(
        parsed,
        json!({"positions":{"a":[30,80],"b":[490,430]},"warnings":[]})
    );

    explicit["edges"][0]["origin"] = json!("uncertain");
    explicit["edges"][0]["evidence"] = json!([]);
    let result = run(folder.path(), &explicit);
    assert!(result.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&result.stdout).unwrap()["warnings"],
        json!(["Uncertain: a → b"])
    );

    // Missing IDs in compact objects must take the normal error path.
    compact["nodes"][0] = json!({"kind":"step","line":1});
    let error = run(folder.path(), &compact);
    assert_eq!(error.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&error.stderr).contains("Missing node id"));
}

#[test]
fn rejects_symlinks_that_escape_source_checkout() {
    let root = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    let file = outside.path().join("other");
    fs::write(&file, "evidence\n").unwrap();
    symlink(file, root.path().join("inside.ts")).unwrap();
    let input = json!({"version":1,"title":"Flow","edgeTypes":{},"edges":[],"regions":[{"id":"r","label":"R"}],
        "nodes":[{"id":"n","title":"A","kind":"step","region":"r","source":{"file":"inside.ts","line":1,
        "sha256":format!("{:x}",Sha256::digest(b"evidence\n"))}}]});
    let result = run(root.path(), &input);
    assert_eq!(result.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&result.stderr).contains("Source escapes checkout"));
}
