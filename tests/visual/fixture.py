"""Offline PR #42, created using two real commits and an isolated review clone."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess


PATHS = {
    "types": "libs/dbtypes/src/document.ts",
    "model": "libs/dbtypes/src/model.ts",
    "spec": "libs/dbtypes/src/model.spec.ts",
    "web": "apps/docs-frontend/src/Review.tsx",
    "shared": "pkgs/docs-shared/src/format.ts",
    "worker": "pkgs/docs-worker/src/publish.ts",
    "other": "README.md",
}
TITLE = "Review document publishing"


def prepare(home):
    state = home / "state" / "code-flow"
    state.mkdir(parents=True)
    (state / "recent-selections.json").write_text(json.dumps(
        [{"mode": "pr", "value": "42", "title": TITLE}]))
    config = home / ".config" / "gtk-3.0"
    config.mkdir(parents=True)
    (config / "settings.ini").write_text(
        "[Settings]\ngtk-enable-animations=false\ngtk-cursor-blink=false\n"
        "gtk-font-name=DejaVu Sans 10\ngtk-xft-dpi=98304\n")
    env = dict(os.environ, HOME=str(home), XDG_STATE_HOME=str(state.parent),
               XDG_CONFIG_HOME=str(home / ".config"), GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="Reviewer", GIT_COMMITTER_NAME="Reviewer",
               GIT_AUTHOR_EMAIL="reviewer@example.invalid",
               GIT_COMMITTER_EMAIL="reviewer@example.invalid",
               GIT_AUTHOR_DATE="2025-01-01T12:00:00Z", GIT_COMMITTER_DATE="2025-01-01T12:00:00Z")

    def git(*args, cwd=home):
        return subprocess.check_output(["git", *args], cwd=cwd, env=env, text=True,
                                       stderr=subprocess.PIPE).strip()

    source = home / "source"
    git("init", "-b", "main", str(source))

    def write(key, content):
        path = source / PATHS[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    write("types", 'export type Document = {\n  status: "draft";\n};\n')
    write("model", 'export const model = "legacy";\n')
    write("worker", 'export const publish = () => false;\n')
    write("other", "Old publishing guide.\n")
    git("add", ".", cwd=source)
    git("commit", "-m", "Create document model", cwd=source)
    git("switch", "-c", "feature/publishing", cwd=source)
    write("types", 'export type Document = {\n  status: "draft" | "published";\n  publishedAt?: string;\n};\n')
    write("model", 'export const model = "document";\n')
    write("spec", 'describe("document publishing", () => {\n  it("publishes a document");\n});\n')
    write("web", 'export function Review() {\n  return <button>Publish</button>;\n}\n')
    write("shared", 'export const format = (name: string) => name.trim();\n')
    write("worker", 'export const publish = () => true;\n')
    (source / PATHS["other"]).unlink()
    git("add", ".", cwd=source)
    git("commit", "-m", "Implement document publishing", cwd=source)
    head = git("rev-parse", "HEAD", cwd=source)
    git("update-ref", "refs/pull/42/head", head, cwd=source)
    git("switch", "main", cwd=source)
    git("clone", "--no-local", str(source), str(home / "plancraft-review"))
    metadata = dict(number=42, title=TITLE,
                    body="## Purpose\n\nLet reviewers follow a document from draft to publication.",
                    url="https://github.com/example/documents/pull/42", state="OPEN", isDraft=False,
                    baseRefName="main", baseRefOid=git("rev-parse", "main", cwd=source), headRefName="feature/publishing", headRefOid=head,
                    reviewDecision="APPROVED", changedFiles=len(PATHS), additions=12, deletions=4,
                    statusCheckRollup=[dict(status="COMPLETED", conclusion="SUCCESS")])
    (home / "pr.json").write_text(json.dumps(metadata))
    bin_dir = home / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] == ['pr', 'view']:
    print((Path(os.environ['HOME']) / 'pr.json').read_text())
elif args[:2] == ['pr', 'list']:
    print('[{"number":42,"state":"OPEN","updatedAt":"2025-01-01"}]')
elif args[:2] == ['api', 'graphql']:
    page = {'nodes': [], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}
    print(json.dumps({'data': {'repository': {'pullRequest': {'id': 'PR_42'}},
                               'root': {k: page for k in ('comments', 'reviews', 'reviewThreads')}}}))
else:
    sys.exit('Unexpected gh invocation ' + str(args))
""")
    gh.chmod(0o755)
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    return env

def prepare_flow(env):
    # Absolute source paths appear on the flow cards. The private container makes
    # this fixture path stable without depending on the host checkout's location.
    root = Path("/tmp/code-flow-example")
    source = Path(env["HOME"]) / "source"
    subprocess.run(["git", "clone", "-q", "-b", "feature/publishing", str(source), str(root)], check=True)
    meta = root / ".tokensave"
    meta.mkdir()
    (meta / "branch-meta.json").write_text(json.dumps({"branches": {"main": {"db_file": "index.sqlite"}}}))
    with sqlite3.connect(meta / "index.sqlite") as db:
        db.execute("CREATE TABLE nodes(id TEXT, name TEXT, kind TEXT, file_path TEXT, start_line INTEGER)")
        db.execute("CREATE TABLE edges(source TEXT, target TEXT, kind TEXT, line INTEGER)")
        db.execute("INSERT INTO nodes VALUES(?, ?, ?, ?, ?)", ("publish", "publish", "function", PATHS["worker"], 1))

    def node(identity, key, line, title, kind, region, hint):
        path = PATHS[key]
        return dict(id=identity, title=title, kind=kind, region=region, hint=hint,
                    source=dict(file=path, line=line, sha256=hashlib.sha256((root / path).read_bytes()).hexdigest()))

    nodes = [node("model", "types", 1, "Read the document", "step", "browser", "Inspect the document model"),
             node("ready", "types", 2, "Ready to publish?", "condition", "browser", "Continue when the document is ready"),
             node("publish", "worker", 1, "Publish the document", "return", "worker", "Return the published state")]
    edges = [{"from": a["id"], "to": b["id"], "type": kind, "origin": "agent",
              "label": label, "evidence": [a["source"], b["source"]]}
             for a, b, kind, label in ((nodes[0], nodes[1], "sequence", ""),
                                      (nodes[1], nodes[2], "queue", "publish"))]
    flow = dict(version=1, title="Document publishing", regions=[dict(id="browser", label="Document editor"),
                 dict(id="worker", label="Publishing worker")], nodes=nodes, edges=edges,
                edgeTypes={"sequence": {"relation": "sequence"}, "queue": {"relation": "handoff"}})
    path = root / "review-flow.json"
    path.write_text(json.dumps(flow))
    return dict(env, CODE_FLOW_ROOT=str(root), CODE_FLOW_FILE=str(path), CODE_FLOW_DIFF_BASE="origin/main")
