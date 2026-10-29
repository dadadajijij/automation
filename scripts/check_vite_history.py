#!/usr/bin/env python3
"""Compare framework config adapters on historical plans using memory-only writes.

Only platform Python is loaded from the trusted baseline Git revision. Project
JavaScript/configs are read as text; no builds, containers or services are run.
This checks configuration adaptation, not runtime redeployment compatibility.
"""

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import runner
from subpath import adapters


def baseline_module(ref):
    revision = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=ROOT,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    source = subprocess.run(
        ["git", "show", f"{revision}:subpath/adapters.py"], cwd=ROOT,
        check=True, capture_output=True, text=True,
    ).stdout
    module = types.ModuleType("subpath._history_baseline")
    module.__package__ = "subpath"
    exec(compile(source, f"{revision}:subpath/adapters.py", "exec"), module.__dict__)
    return revision, module


def compare_job(result_path, baseline, *, shared_repo=False):
    result = json.loads(result_path.read_text())
    repo = ((result_path.parents[2] / "repo") if shared_repo else (result_path.parent.parent / "work-repo")).resolve()
    payload = (result.get("artifacts") or {}).get("subpath_plan")
    plan = runner.build_runtime_subpath_plan(repo) if shared_repo else runner.subpath_plan_from_payload(payload, repo)
    if plan is None or not plan.projects or not repo.is_dir():
        return {"job": str(result_path.relative_to(ROOT)), "outcome": "insufficient_evidence"}
    if any(not project.project_root.resolve().is_relative_to(repo) for project in plan.projects):
        return {"job": str(result_path.relative_to(ROOT)), "outcome": "insufficient_evidence"}

    original = {}

    def checked_path(path):
        path = Path(path).resolve()
        if not path.is_relative_to(repo):
            raise ValueError(f"historical plan refers outside its work-repo: {path}")
        return path

    def run(module, candidate=False):
        overlay = {}
        report = []

        def read(path):
            path = checked_path(path)
            if path in overlay:
                return overlay[path]
            if path not in original:
                original[path] = path.read_text() if path.is_file() else ""
            return original[path]

        def write(path, content):
            path = checked_path(path)
            read(path)
            overlay[path] = content

        def package(path):
            source = read(Path(path) / "package.json")
            return json.loads(source) if source else {}

        kwargs = dict(parse_package_json=package, read_text=read, write_text=write,
                      preview_allowed_host=runner.DEFAULT_EXTERNAL_ACCESS_HOST)
        if candidate:
            kwargs.update(deployment_root=repo, adaptation_report=report)
        module.apply_framework_config_adapters(plan, result["project_slug"], **kwargs)
        first = dict(overlay)
        first_report = list(report)
        module.apply_framework_config_adapters(plan, result["project_slug"], **kwargs)
        idempotent = all(overlay.get(path, original.get(path, "")) == first.get(path, original.get(path, ""))
                         for path in overlay.keys() | first.keys())
        return first, first_report, idempotent

    old, _, old_idempotent = run(baseline)
    new, decisions, new_idempotent = run(adapters, candidate=True)
    differences = []
    for path in sorted(old.keys() | new.keys()):
        before, after = old.get(path, original[path]), new.get(path, original[path])
        if before != after:
            label = path.relative_to(repo).as_posix()
            differences.append({"file": label, "diff": "".join(difflib.unified_diff(
                before.splitlines(True), after.splitlines(True),
                fromfile=f"baseline/{label}", tofile=f"candidate/{label}", n=2,
            ))})
    for item in decisions:
        item["config_file"] = Path(item["config_file"]).relative_to(repo).as_posix()
    unchanged_on_disk = all(hashlib.sha256(path.read_text().encode()).digest() == hashlib.sha256(content.encode()).digest()
                            for path, content in original.items() if path.is_file())
    return {
        "project": result["project_slug"], "job": result_path.parent.parent.name,
        "input_kind": "shared_source" if shared_repo else "historical_work_repo",
        "historical_status": result.get("status"),
        "outcome": "configuration_changed" if differences else "configuration_identical",
        "differences": differences, "vite_decisions": decisions,
        "baseline_idempotent": old_idempotent, "candidate_idempotent": new_idempotent,
        "source_files_unchanged": unchanged_on_disk,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="HEAD", help="Trusted platform Git revision before the repair")
    parser.add_argument("--output", type=Path, help="Write the JSON report outside jobs/")
    parser.add_argument("--include-shared-repos", action="store_true", help="Also compare freshly detected plans from each current shared repo")
    args = parser.parse_args()
    if args.output and args.output.resolve().is_relative_to((ROOT / "jobs").resolve()):
        parser.error("the report must be written outside jobs/")
    revision, baseline = baseline_module(args.baseline_ref)
    jobs = []
    result_paths = sorted((ROOT / "jobs").glob("*/deploy-*/output/result.json"))
    cases = [(path, False) for path in result_paths]
    if args.include_shared_repos:
        latest = {}
        for path in result_paths:
            latest[path.parents[2]] = path
        cases.extend((path, True) for path in latest.values())
    for path, shared_repo in cases:
        try:
            jobs.append(compare_job(path, baseline, shared_repo=shared_repo))
        except (OSError, ValueError, KeyError) as error:
            jobs.append({"job": str(path.relative_to(ROOT)), "outcome": "insufficient_evidence", "error": str(error)})
    summary = {
        "cases": len(jobs),
        "historical_jobs": len(result_paths),
        "shared_repos": len(cases) - len(result_paths),
        "configuration_identical": sum(item["outcome"] == "configuration_identical" for item in jobs),
        "configuration_changed": sum(item["outcome"] == "configuration_changed" for item in jobs),
        "insufficient_evidence": sum(item["outcome"] == "insufficient_evidence" for item in jobs),
        "candidate_non_idempotent": sum(item.get("candidate_idempotent") is False for item in jobs),
        "source_files_modified": sum(item.get("source_files_unchanged") is False for item in jobs),
    }
    report = {"baseline_revision": revision, "scope": "framework_config_adapters_only", "summary": summary, "jobs": jobs}
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output)
    else:
        print(output, end="")
    print(json.dumps(summary), file=sys.stderr)
    return int(bool(summary["insufficient_evidence"] or summary["candidate_non_idempotent"] or summary["source_files_modified"]))


if __name__ == "__main__":
    raise SystemExit(main())
