"""Offline layout of the 123-file review at the time of the navigation report.

The JSON contains aliased paths in the original tree order, status, viewed flags,
and zero-content hunk geometry. Each hunk stores its first old line and runs of
context (=), addition (+) or removal (-) with the original character widths.
All displayed rows fit below the canvas's 300-column wrapping threshold.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

from fixture import environment


TITLE = "Navigation regression 28118"
NUMBER = 28118
PROFILE = json.loads(Path(__file__).with_suffix(".json").read_text())
LANES = {lane: tuple(files) for lane, files in PROFILE.items()}
FIRST_PATHS = tuple(paths[0] for paths in LANES.values())


def revisions(file_index, hunks):
    """Generate distinct lines so Git keeps the recorded edits and hunk boundaries."""
    old, new = [], []
    sequence = 0

    def line(width):
        nonlocal sequence
        sequence += 1
        # A unique marker on short lines prevents Git from matching unrelated edits.
        # At least one longer line determines the width of every file's card.
        token = f"{file_index:02x}{sequence:04x}"
        return (token + " sample entry" * (width // 13 + 1))[:max(len(token), width)] + "\n"

    for start, runs in hunks:
        for _ in range(len(old) + 1, start):
            unchanged = line(24)
            old.append(unchanged)
            new.append(unchanged)
        for op, widths in re.findall(r"([=+-])([0-9,]+)", runs):
            for width in map(int, widths.split(",")):
                row = line(width)
                if op != "+":
                    old.append(row)
                if op != "-":
                    new.append(row)
    return "".join(old), "".join(new)


def prepare_pr_28118(home):
    env = environment(home, NUMBER, TITLE)
    review = home / "plancraft-review"
    env.update(CODE_FLOW_REVIEW_ROOT=str(review), CODE_FLOW_REPO="plancraft/plancraft",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_DEFAULT_HASH="sha1")
    state = home / "state" / "code-flow"
    (state / "help-seen").touch()
    (state / "sidebar-hidden").touch()
    source = home / "source"

    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], env=env,
                                       stderr=subprocess.PIPE, text=True)

    source.mkdir()
    git("init", "-b", "main")
    files = [(p, info) for lane in PROFILE.values() for p, info in lane.items()]
    for i, (path, info) in enumerate(files):
        if info["status"] != "A":
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            old, _ = revisions(i, info["hunks"])
            target.write_text(old, encoding="utf-8")

    git("add", ".")
    git("commit", "-m", "Create sample files")
    base = git("rev-parse", "HEAD").strip()
    branch = "feature/navigation-28118"
    git("switch", "-c", branch)
    for i, (path, info) in enumerate(files):
        target = source / path
        if info["status"] == "D":
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            _, new = revisions(i, info["hunks"])
            target.write_text(new, encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Update sample files")
    head = git("rev-parse", "HEAD").strip()
    git("update-ref", f"refs/pull/{NUMBER}/head", head)
    git("switch", "main")
    git("clone", "--no-local", str(source), str(review))

    with sqlite3.connect(state / "review-progress.sqlite3") as db:
        db.execute("CREATE TABLE viewed(scope TEXT, path TEXT, fingerprint TEXT, "
                   "reviewed_head TEXT, file_commit TEXT, PRIMARY KEY(scope,path))")
        for path, info in files:
            if info.get("viewed"):
                patch = git("diff", "--no-ext-diff", "--no-textconv", "--no-color",
                            "--find-renames", "--unified=3", base, head, "--", path)
                digest = hashlib.sha256(json.dumps([info["status"], path, path, patch]).encode())
                db.execute("INSERT INTO viewed VALUES (?,?,?,?,?)",
                           (f"{env['CODE_FLOW_REPO']}:pr:{NUMBER}", path, digest.hexdigest(),
                            head, head))

    metadata = dict(number=NUMBER, title=TITLE, body="Exercise navigation on a mixed-height map.",
                    url=f"https://github.com/example/documents/pull/{NUMBER}", state="OPEN",
                    isDraft=False, baseRefName="main", baseRefOid=base, headRefName=branch,
                    headRefOid=head, changedFiles=123, additions=3586, deletions=693,
                    reviewDecision="", statusCheckRollup=[])
    (home / "pr.json").write_text(json.dumps(metadata))
    return env
