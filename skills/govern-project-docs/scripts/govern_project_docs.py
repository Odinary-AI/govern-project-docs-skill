#!/usr/bin/env python3
"""Minimal deterministic checks for govern-project-docs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def load_json_or_missing(path: Path) -> tuple[dict | None, dict | None]:
    try:
        return load_json(path), None
    except FileNotFoundError:
        return None, adapter_missing_result(path)


def emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def path_matches(candidate: str, pattern: str) -> bool:
    pattern = pattern.rstrip("/")
    candidate = candidate.rstrip("/")
    return candidate == pattern or candidate.startswith(pattern + "/")


def normalize_path_value(path: str, field: str = "path") -> tuple[str | None, dict | None]:
    raw = path.strip()
    if not raw:
        return None, {"code": "invalid-path-empty", "field": field, "path": path}
    if "\x00" in raw:
        return None, {"code": "invalid-path-nul", "field": field, "path": path}
    candidate = raw
    while candidate.startswith("./"):
        candidate = candidate[2:]
    candidate = candidate.rstrip("/")
    if not candidate:
        return None, {"code": "invalid-path-empty", "field": field, "path": path}
    if Path(candidate).is_absolute():
        return None, {"code": "invalid-path-absolute", "field": field, "path": path}
    if any(part == ".." for part in Path(candidate).parts):
        return None, {"code": "invalid-path-escape", "field": field, "path": path}
    return candidate, None


def normalize_path(path: str) -> str:
    normalized, _ = normalize_path_value(path)
    return normalized or path.strip().rstrip("/")


def normalize_paths_with_findings(paths: list[str], field: str) -> tuple[list[str], list[dict]]:
    normalized = []
    findings = []
    for path in paths:
        value, finding = normalize_path_value(path, field)
        if finding:
            findings.append(finding)
        elif value:
            normalized.append(value)
    return sorted(set(normalized)), findings


def normalize_paths(paths: list[str]) -> list[str]:
    normalized, _ = normalize_paths_with_findings(paths, "path")
    return normalized


def is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def add_type_finding(findings: list[dict], code: str, field: str, expected: str) -> None:
    findings.append({"code": code, "field": field, "expected": expected})


def safe_rule_list(adapter: dict) -> list[dict]:
    rules = adapter.get("authority_rules", [])
    return rules if isinstance(rules, list) else []


def safe_section(adapter: dict, key: str) -> dict:
    value = adapter.get(key, {})
    return value if isinstance(value, dict) else {}


def adapter_missing_result(path: Path) -> dict:
    return {
        "result": "unproven",
        "adapter": {"path": str(path)},
        "mechanical_findings": [{"code": "adapter-missing", "path": str(path)}],
        "semantic_findings": [],
        "human_approval_required": [],
        "recovery": "No project adapter was found. Create a candidate adapter from project pointers and ask for human approval before treating it as governance authority.",
    }


def rule_map(adapter: dict) -> dict:
    return {
        rule["id"]: rule
        for rule in safe_rule_list(adapter)
        if isinstance(rule, dict) and rule.get("id")
    }


def make_semantic_finding(
    *,
    code: str,
    affected_question: str | None,
    evidence: str,
    confidence: str,
    human_boundary: bool,
) -> dict:
    if human_boundary:
        decision_boundary = "human"
        suggested = "route to human decision; do not auto-repair"
    else:
        decision_boundary = "semantic"
        suggested = "review authority and evidence; keep result unproven until reconciled"
    return {
        "code": code,
        "affected_question": affected_question,
        "evidence": evidence,
        "confidence": confidence,
        "decision_boundary": decision_boundary,
        "suggested_handling": suggested,
        "human_boundary": human_boundary,
    }


def validate_adapter(adapter: dict) -> dict:
    findings = []
    raw_rules = adapter.get("authority_rules", [])
    rules = raw_rules if isinstance(raw_rules, list) else []
    seen = set()

    if adapter.get("schema_version") != "1":
        findings.append({"code": "unsupported-schema-version", "message": "schema_version must be 1"})

    if not isinstance(adapter.get("project"), str) or not adapter.get("project"):
        add_type_finding(findings, "invalid-project", "project", "non-empty string")

    if not isinstance(raw_rules, list) or not raw_rules:
        findings.append({"code": "missing-authority-rules", "message": "authority_rules must be a non-empty list"})

    for rule in rules:
        if not isinstance(rule, dict):
            findings.append({"code": "invalid-authority-rule", "message": "authority rule must be an object"})
            continue
        rid = rule.get("id")
        if not rid:
            findings.append({"code": "missing-authority-rule-id", "message": "authority rule is missing id"})
            continue
        if rid in seen:
            findings.append({"code": "duplicate-authority-rule", "id": rid})
        seen.add(rid)
        if not rule.get("question") or not rule.get("scope"):
            findings.append({"code": "incomplete-authority-rule", "id": rid})
        if not isinstance(rule.get("paths"), list) or not rule.get("paths"):
            findings.append({"code": "missing-authority-paths", "id": rid})
        elif not is_string_list(rule.get("paths")):
            add_type_finding(findings, "invalid-authority-paths", f"authority_rules.{rid}.paths", "list of strings")
        if "triggers" in rule and not is_string_list(rule.get("triggers")):
            add_type_finding(findings, "invalid-authority-triggers", f"authority_rules.{rid}.triggers", "list of strings")
        if "human_approval_types" in rule:
            if not is_string_list(rule.get("human_approval_types")):
                add_type_finding(findings, "invalid-authority-human-approval-types", f"authority_rules.{rid}.human_approval_types", "list of strings")
            elif is_string_list(adapter.get("human_approval")):
                for approval_type in rule.get("human_approval_types"):
                    if approval_type not in adapter.get("human_approval", []):
                        findings.append({
                            "code": "undeclared-authority-human-approval-type",
                            "id": rid,
                            "type": approval_type,
                        })

    entrypoints = adapter.get("entrypoints")
    if not isinstance(entrypoints, dict):
        findings.append({"code": "missing-entrypoints", "message": "entrypoints must be an object"})
        entrypoints = {}
    for key in ["current", "historical", "evidence"]:
        value = entrypoints.get(key)
        if not is_string_list(value):
            add_type_finding(findings, f"invalid-{key}-entrypoints", f"entrypoints.{key}", "list of strings")
    if is_string_list(entrypoints.get("current")) and not entrypoints.get("current"):
        findings.append({"code": "missing-current-entrypoints", "message": "entrypoints.current must not be empty"})
    if is_string_list(entrypoints.get("evidence")) and not entrypoints.get("evidence"):
        findings.append({"code": "missing-evidence-entrypoints", "message": "entrypoints.evidence must not be empty"})

    boundaries = adapter.get("boundaries")
    if not isinstance(boundaries, dict):
        findings.append({"code": "missing-boundaries", "message": "boundaries must be an object"})
        boundaries = {}
    for key in ["protected", "excluded", "ordinary_docs"]:
        value = boundaries.get(key)
        if not is_string_list(value):
            add_type_finding(findings, f"invalid-boundary-{key}", f"boundaries.{key}", "list of strings")

    human_approval = adapter.get("human_approval")
    if human_approval is None:
        findings.append({"code": "missing-human-approval", "message": "human_approval must be a list"})
    elif not is_string_list(human_approval):
        add_type_finding(findings, "invalid-human-approval", "human_approval", "list of strings")

    checks = adapter.get("plan_status_checks", [])
    if checks is not None:
        if not isinstance(checks, list):
            add_type_finding(findings, "invalid-plan-status-checks", "plan_status_checks", "list")
        else:
            for index, check in enumerate(checks):
                if not isinstance(check, dict):
                    add_type_finding(findings, "invalid-plan-status-check", f"plan_status_checks.{index}", "object")
                    continue
                if not isinstance(check.get("status_path"), str):
                    add_type_finding(findings, "invalid-plan-status-path", f"plan_status_checks.{index}.status_path", "string")
                if not is_string_list(check.get("plan_paths")):
                    add_type_finding(findings, "invalid-plan-status-plan-paths", f"plan_status_checks.{index}.plan_paths", "list of strings")

    return {
        "result": "fail" if findings else "pass",
        "adapter": {
            "schema_version": adapter.get("schema_version"),
            "project": adapter.get("project"),
            "authority_rule_count": len(rules),
        },
        "mechanical_findings": findings,
        "semantic_findings": [],
        "human_approval_required": [],
        "recovery": "Adapter validation completed from declared pointers and rules.",
    }


def validate_adapter_command(args: argparse.Namespace) -> None:
    adapter, missing = load_json_or_missing(Path(args.adapter))
    emit(missing if missing else validate_adapter(adapter))


def assertion_finding(
    assertion: dict,
    adapter: dict,
    workspace: Path,
    affected_question: str | None,
) -> tuple[str | None, dict | None]:
    atype = assertion["type"]

    if atype == "path_exists":
        path = workspace / assertion["path"]
        if not path.exists():
            return "mechanical", {"code": "missing-required-path", "path": assertion["path"]}
        return None, None

    if atype == "path_missing":
        path = workspace / assertion["path"]
        if not path.exists():
            return "mechanical", {"code": "missing-required-path", "path": assertion["path"]}
        return None, None

    if atype == "distinct_authorities":
        rules = rule_map(adapter)
        ids = assertion["ids"]
        missing = [rid for rid in ids if rid not in rules]
        if missing:
            return "mechanical", {"code": "missing-authority-rule", "ids": missing}
        path_sets = [tuple(rules[rid].get("paths", [])) for rid in ids]
        if len(set(path_sets)) != len(path_sets):
            return "mechanical", {"code": "competing-authority", "ids": ids}
        return None, None

    if atype == "ordered_authority":
        rule = rule_map(adapter).get(assertion["id"])
        if not rule:
            return "mechanical", {"code": "missing-authority-rule", "id": assertion["id"]}
        if rule.get("paths") != assertion["paths"]:
            return "mechanical", {"code": "authority-order-mismatch", "id": assertion["id"]}
        return None, None

    if atype == "current_path_under_historical":
        return "mechanical", {"code": "historical-material-in-current-location", "path": assertion["path"]}

    if atype == "text_contains":
        path = workspace / assertion["path"]
        if not path.exists():
            return "mechanical", {"code": "missing-evidence-path", "path": assertion["path"]}
        text = path.read_text(encoding="utf-8")
        if assertion["text"] not in text:
            return "mechanical", {"code": "missing-evidence-text", "path": assertion["path"]}
        layer = assertion.get("layer", "none")
        if layer == "semantic":
            return "semantic", make_semantic_finding(
                code="semantic-review-required",
                affected_question=affected_question,
                evidence=assertion["path"],
                confidence="high",
                human_boundary=False,
            )
        if layer == "human":
            return "human", make_semantic_finding(
                code="human-decision-required",
                affected_question=affected_question,
                evidence=assertion["path"],
                confidence="high",
                human_boundary=True,
            )
        return None, None

    return "mechanical", {"code": "unknown-assertion-type", "type": atype}


def run_one_case(case: dict, adapter: dict, workspace: Path) -> dict:
    mechanical = []
    semantic = []
    human_required = []

    if case.get("question_id") not in rule_map(adapter):
        mechanical.append({"code": "unmapped-question", "id": case.get("question_id")})

    for assertion in case.get("assertions", []):
        layer, finding = assertion_finding(assertion, adapter, workspace, case.get("question_id"))
        if not finding:
            continue
        if layer == "mechanical":
            mechanical.append(finding)
        elif layer == "semantic":
            semantic.append(finding)
        elif layer == "human":
            semantic.append(finding)
            human_required.append(finding["code"])

    human_boundary = bool(case.get("human") or human_required)
    if mechanical:
        result = "fail"
    elif semantic or human_boundary:
        result = "unproven"
    else:
        result = "pass"

    return {
        "id": case["id"],
        "question_id": case.get("question_id"),
        "result": result,
        "expected": case.get("expected"),
        "mechanical_findings": mechanical,
        "semantic_findings": semantic,
        "human_boundary": human_boundary,
    }


def summarize(cases: list[dict]) -> dict:
    return {
        "total": len(cases),
        "pass": sum(1 for case in cases if case["result"] == "pass"),
        "fail": sum(1 for case in cases if case["result"] == "fail"),
        "unproven": sum(1 for case in cases if case["result"] == "unproven"),
        "mechanical": sum(1 for case in cases if case["mechanical_findings"]),
        "semantic": sum(1 for case in cases if case["semantic_findings"]),
        "human": sum(1 for case in cases if case["human_boundary"]),
    }


def run_cases(args: argparse.Namespace) -> dict:
    adapter, missing = load_json_or_missing(Path(args.adapter))
    if missing:
        return {
            **missing,
            "summary": {
                "total": 0,
                "pass": 0,
                "fail": 0,
                "unproven": 0,
                "mechanical": 0,
                "semantic": 0,
                "human": 0,
            },
            "cases": [],
        }
    cases_doc = load_json(Path(args.cases))
    workspace = Path(args.workspace)
    adapter_result = validate_adapter(adapter)
    cases = [run_one_case(case, adapter, workspace) for case in cases_doc.get("cases", [])]
    mismatches = [
        {"id": case["id"], "expected": case["expected"], "actual": case["result"]}
        for case in cases
        if case.get("expected") and case["expected"] != case["result"]
    ]
    summary = summarize(cases)
    all_cases_have_expectations = all(case.get("expected") for case in cases)
    if adapter_result["result"] == "fail" or mismatches:
        result = "fail"
    elif all_cases_have_expectations:
        result = "pass"
    elif summary["fail"]:
        result = "fail"
    elif summary["unproven"]:
        result = "unproven"
    else:
        result = "pass"
    return {
        "result": result,
        "summary": summary,
        "cases": cases,
        "mechanical_findings": adapter_result["mechanical_findings"] + mismatches,
        "semantic_findings": [finding for case in cases for finding in case["semantic_findings"]],
        "human_approval_required": sorted({finding for case in cases if case["human_boundary"] for finding in ["human decision"]}),
        "recovery": "Case validation completed; use failed and unproven cases as next-batch inputs.",
    }


def run_cases_command(args: argparse.Namespace) -> None:
    emit(run_cases(args))


LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def markdown_links(text: str) -> list[str]:
    links = []
    for match in LINK_RE.finditer(text):
        raw = match.group(1).strip()
        if not raw:
            continue
        # Drop optional Markdown title after a whitespace separator.
        target = raw.split()[0]
        links.append(target.strip("<>"))
    return links


def is_local_link(target: str) -> bool:
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return False
    return not target.startswith("#") and not target.startswith("mailto:")


def resolve_link(doc_path: Path, target: str) -> Path:
    clean = unquote(target.split("#", 1)[0])
    return (doc_path.parent / clean).resolve()


def current_authority_docs(adapter: dict, workspace: Path) -> list[Path]:
    docs = []
    for entry in safe_section(adapter, "entrypoints").get("current", []):
        path = workspace / entry
        if path.is_file() and path.suffix.lower() == ".md":
            docs.append(path)
    for rule in safe_rule_list(adapter):
        for pointer in rule.get("paths", []):
            path = workspace / pointer
            if path.is_file() and path.suffix.lower() == ".md":
                docs.append(path)
    unique = []
    seen = set()
    for doc in docs:
        resolved = doc.resolve()
        if resolved not in seen:
            unique.append(doc)
            seen.add(resolved)
    return unique


def line_status(text: str) -> str | None:
    for line in text.splitlines():
        match = re.match(r"\s*Status:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def says_no_active_batch(text: str) -> bool:
    return bool(re.search(r"\bno\b.+\b(batch|project batch|work|task)\b.+\bactive\b", text, flags=re.IGNORECASE))


def is_active_status(status: str | None) -> bool:
    return bool(status and re.search(r"\bactive\b", status, flags=re.IGNORECASE))


def check_plan_status(adapter: dict, workspace: Path) -> list[dict]:
    findings = []
    for check in adapter.get("plan_status_checks", []) or []:
        if not isinstance(check, dict):
            continue
        status_path = check.get("status_path")
        plan_paths = check.get("plan_paths", [])
        if not isinstance(status_path, str) or not is_string_list(plan_paths):
            continue
        status_file = workspace / status_path
        if not status_file.exists():
            findings.append({"code": "missing-plan-status-source", "path": status_path})
            continue
        try:
            status_text = status_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append({"code": "unreadable-plan-status-source", "path": status_path})
            continue
        if not says_no_active_batch(status_text):
            continue
        for plan_path in plan_paths:
            plan_file = workspace / plan_path
            if not plan_file.exists():
                findings.append({"code": "missing-plan-status-target", "path": plan_path})
                continue
            try:
                plan_text = plan_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                findings.append({"code": "unreadable-plan-status-target", "path": plan_path})
                continue
            if is_active_status(line_status(plan_text)):
                findings.append({
                    "code": "plan-status-conflict",
                    "status_path": status_path,
                    "plan_path": plan_path,
                    "message": "status says no active batch while a mapped plan says active",
                })
    return findings


def diagnose(adapter: dict, workspace: Path) -> dict:
    adapter_result = validate_adapter(adapter)
    findings = list(adapter_result["mechanical_findings"])

    checked_targets = set()
    for rule in safe_rule_list(adapter):
        for pointer in rule.get("paths", []):
            target = workspace / pointer
            checked_targets.add(pointer)
            if not target.exists():
                findings.append({"code": "missing-mapped-target", "path": pointer, "authority": rule.get("id")})

    entrypoints = safe_section(adapter, "entrypoints")
    for entry in entrypoints.get("current", []):
        target = workspace / entry
        checked_targets.add(entry)
        if not target.exists():
            findings.append({"code": "missing-current-entrypoint", "path": entry})
        if any(path_matches(entry, historical) for historical in entrypoints.get("historical", [])):
            findings.append({"code": "historical-material-configured-current", "path": entry})

    for entry in entrypoints.get("evidence", []):
        target = workspace / entry
        checked_targets.add(entry)
        if not target.exists():
            findings.append({"code": "missing-evidence-entrypoint", "path": entry})

    findings.extend(check_plan_status(adapter, workspace))

    link_checked = 0
    broken = 0
    for doc in current_authority_docs(adapter, workspace):
        try:
            text = doc.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append({"code": "unreadable-current-authority-doc", "path": str(doc.relative_to(workspace))})
            continue
        for target in markdown_links(text):
            if not is_local_link(target):
                continue
            link_checked += 1
            resolved = resolve_link(doc, target)
            if not resolved.exists():
                broken += 1
                findings.append({
                    "code": "broken-current-authority-link",
                    "path": str(doc.relative_to(workspace)),
                    "target": target,
                })

    return {
        "result": "fail" if findings else "pass",
        "coverage": {
            "workspace_mode": "live",
            "authority_rules": len(safe_rule_list(adapter)),
            "mapped_targets": len(checked_targets),
            "current_authority_docs": len(current_authority_docs(adapter, workspace)),
            "proves": [
                "adapter structure",
                "mapped authority targets",
                "current and evidence entrypoints",
                "configured plan status checks",
                "local links in current authority markdown documents",
            ],
            "does_not_prove": [
                "semantic consistency between documents and implementation",
                "that the current task has completed Closeout",
                "that product, architecture, release, or human approval meaning is correct",
            ],
        },
        "link_check": {
            "checked": link_checked,
            "broken": broken,
            "scope": "current authority markdown only",
        },
        "mechanical_findings": findings,
        "semantic_findings": [],
        "human_approval_required": [],
        "recovery": "Live diagnostic completed without modifying the workspace.",
    }


def diagnose_command(args: argparse.Namespace) -> None:
    adapter, missing = load_json_or_missing(Path(args.adapter))
    emit(missing if missing else diagnose(adapter, Path(args.workspace)))


def impact_command(args: argparse.Namespace) -> None:
    adapter, missing = load_json_or_missing(Path(args.adapter))
    changed_paths, path_findings = normalize_paths_with_findings(args.changed_path, "changed_path")
    if missing:
        missing["impact"] = {
            "changed_paths": changed_paths,
            "affected_authorities": [],
            "protected_paths": [],
            "excluded_paths": [],
            "evidence_entrypoints": [],
        }
        emit(missing)
        return

    adapter_result = validate_adapter(adapter)
    if adapter_result["result"] == "fail":
        emit({
            "result": "fail",
            "impact": {
                "changed_paths": changed_paths,
                "affected_authorities": [],
                "protected_paths": [],
                "excluded_paths": [],
                "evidence_entrypoints": [],
            },
            "mechanical_findings": adapter_result["mechanical_findings"],
            "semantic_findings": [],
            "human_approval_required": [],
            "recovery": "Impact cannot run until the project adapter validates.",
        })
        return

    rules = safe_rule_list(adapter)
    boundary_rules = safe_section(adapter, "boundaries")
    protected_patterns = boundary_rules.get("protected", [])
    excluded_patterns = boundary_rules.get("excluded", [])
    affected = []
    protected = []
    excluded = []

    for changed in changed_paths:
        for pattern in protected_patterns:
            if path_matches(changed, pattern):
                protected.append(changed)
        for pattern in excluded_patterns:
            if path_matches(changed, pattern):
                excluded.append(changed)
        for rule in rules:
            if any(path_matches(changed, path) or path_matches(path, changed) for path in rule.get("paths", [])):
                affected.append(rule["id"])
            if any(path_matches(changed, trigger) for trigger in rule.get("triggers", []) or []):
                affected.append(rule["id"])

    affected = sorted(set(affected))
    human = []
    for rule in rules:
        if rule.get("id") not in affected:
            continue
        if rule.get("human_approval_types"):
            human.extend(rule.get("human_approval_types", []))
        elif rule.get("human"):
            human.extend(adapter.get("human_approval", []))
    human = sorted(set(human))
    candidate_authority_paths = sorted({
        path
        for rule in rules
        if rule.get("id") in affected
        for path in rule.get("paths", [])
    })
    receipt = None
    receipt_findings = []
    if args.workspace:
        workspace = Path(args.workspace)
        source = args.change_source
        if source == "auto":
            git_inventory, git_findings = git_status_inventory(workspace)
            if git_findings:
                source = "filesystem"
            else:
                source = "git"
        if source == "git":
            baseline_inventory, receipt_findings = scan_filesystem_inventory(workspace, adapter, source_kind="git-baseline")
            git_inventory, git_findings = git_status_inventory(workspace)
            baseline_dirty = set(inventory_event_paths(git_inventory.get("entries", []))) if not git_findings else set()
            for entry in baseline_inventory.get("entries", []):
                if entry["path"] in baseline_dirty:
                    entry.setdefault("metadata", {})["dirty_at_baseline"] = True
            receipt_findings.extend(git_findings)
            source_metadata = git_inventory.get("source", {}).get("metadata", {})
        elif source == "filesystem":
            baseline_inventory, receipt_findings = scan_filesystem_inventory(workspace, adapter, source_kind="filesystem")
            source_metadata = {}
        else:
            baseline_inventory = {"schema": "govern-project-docs.inventory.v1", "source": {"kind": source, "verified": False}, "entries": []}
            source_metadata = {}
        receipt = {
            "schema": "govern-project-docs.receipt.v1",
            "adapter": {"project": adapter.get("project"), "schema_version": adapter.get("schema_version")},
            "workspace": {"path": str(workspace.resolve())},
            "inventory_source": {
                "kind": source,
                "verified": not receipt_findings and source in {"git", "filesystem"},
                "metadata": source_metadata,
            },
            "baseline_inventory": baseline_inventory,
            "planned_paths": changed_paths,
            "affected_authorities": affected,
            "candidate_authority_paths": candidate_authority_paths,
            "protected_paths": sorted(set(protected)),
            "excluded_paths": sorted(set(excluded)),
            "human_approval_required": human,
            "verification_capability": {
                "baseline_inventory": source in {"git", "filesystem"},
                "event_isolation": source in {"git", "filesystem"},
            },
            "recovery": "Pass this receipt to Closeout with --receipt, plus actual changed paths and event-authorized documents.",
        }

    emit({
        "result": "fail" if path_findings or receipt_findings else "unproven" if human or protected or excluded else "pass",
        "impact": {
            "changed_paths": changed_paths,
            "affected_authorities": affected,
            "candidate_authority_paths": candidate_authority_paths,
            "protected_paths": sorted(set(protected)),
            "excluded_paths": sorted(set(excluded)),
            "evidence_entrypoints": safe_section(adapter, "entrypoints").get("evidence", []),
        },
        "receipt": receipt,
        "mechanical_findings": path_findings + receipt_findings,
        "semantic_findings": [],
        "human_approval_required": human,
        "recovery": "Impact completed; run Closeout before declaring completion.",
    })


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory_entry_paths(entry: dict) -> list[str]:
    if entry.get("kind") == "renamed":
        return [entry["old_path"], entry["new_path"]]
    return [entry["path"]]


def inventory_event_paths(entries: list[dict]) -> list[str]:
    paths = []
    for entry in entries:
        paths.extend(inventory_entry_paths(entry))
    return sorted(set(paths))


def scan_filesystem_inventory(
    workspace: Path,
    adapter: dict,
    *,
    source_kind: str = "filesystem",
    extra_excluded: list[str] | None = None,
) -> tuple[dict, list[dict]]:
    workspace = workspace.resolve()
    excluded_patterns = list(safe_section(adapter, "boundaries").get("excluded", []))
    excluded_patterns.extend(extra_excluded or [])
    normalized_excluded, excluded_findings = normalize_paths_with_findings(excluded_patterns, "excluded")
    findings = list(excluded_findings)
    entries = []

    for path in sorted(workspace.rglob("*")):
        if path.is_dir():
            continue
        try:
            relative = path.relative_to(workspace).as_posix()
        except ValueError:
            continue
        normalized, finding = normalize_path_value(relative, "inventory.path")
        if finding:
            findings.append(finding)
            continue
        if any(path_matches(normalized, pattern) for pattern in normalized_excluded):
            continue
        if path.is_symlink():
            target = path.resolve()
            try:
                target.relative_to(workspace)
            except ValueError:
                findings.append({"code": "symlink-target-outside-workspace", "path": normalized})
                continue
            digest = f"symlink:{target.relative_to(workspace).as_posix()}"
        else:
            try:
                digest = file_digest(path)
            except OSError as exc:
                findings.append({"code": "unreadable-inventory-path", "path": normalized, "message": str(exc)})
                continue
        entries.append({
            "path": normalized,
            "existence": True,
            "digest": digest,
            "inventory_source": source_kind,
            "verified": True,
            "metadata": {},
        })

    return {
        "schema": "govern-project-docs.inventory.v1",
        "source": {"kind": source_kind, "verified": not findings},
        "entries": entries,
    }, findings


def inventory_map(inventory: dict) -> dict[str, dict]:
    result = {}
    for entry in inventory.get("entries", []) or []:
        if not isinstance(entry, dict):
            continue
        path, finding = normalize_path_value(str(entry.get("path", "")), "inventory.path")
        if finding or not path:
            continue
        normalized = dict(entry)
        normalized["path"] = path
        result[path] = normalized
    return result


def compare_inventories(baseline: dict, final: dict) -> tuple[list[dict], list[str]]:
    before = inventory_map(baseline)
    after = inventory_map(final)
    paths = sorted(set(before) | set(after))
    changes = []
    pre_existing_unchanged = []
    for path in paths:
        old = before.get(path)
        new = after.get(path)
        if old and not new:
            changes.append({
                "path": path,
                "kind": "deleted",
                "existence": False,
                "digest": old.get("digest"),
                "inventory_source": final.get("source", {}).get("kind", "inventory"),
                "verified": final.get("source", {}).get("verified", True),
                "metadata": {"dirty_at_baseline": bool((old.get("metadata") or {}).get("dirty_at_baseline"))},
            })
        elif new and not old:
            changes.append({
                "path": path,
                "kind": "added",
                "existence": True,
                "digest": new.get("digest"),
                "inventory_source": final.get("source", {}).get("kind", "inventory"),
                "verified": final.get("source", {}).get("verified", True),
                "metadata": {},
            })
        elif old and new and old.get("digest") != new.get("digest"):
            changes.append({
                "path": path,
                "kind": "modified",
                "existence": True,
                "digest": new.get("digest"),
                "inventory_source": final.get("source", {}).get("kind", "inventory"),
                "verified": final.get("source", {}).get("verified", True),
                "metadata": {"dirty_at_baseline": bool((old.get("metadata") or {}).get("dirty_at_baseline"))},
            })
        elif old and new:
            if (old.get("metadata") or {}).get("dirty_at_baseline"):
                pre_existing_unchanged.append(path)
    return changes, pre_existing_unchanged


def load_inventory_file(path: str | None) -> tuple[dict | None, list[dict]]:
    if not path:
        return None, []
    try:
        inventory = load_json(Path(path))
    except FileNotFoundError:
        return None, [{"code": "inventory-file-missing", "path": path}]
    if not isinstance(inventory.get("entries"), list):
        return None, [{"code": "invalid-inventory", "path": path, "message": "entries must be a list"}]
    return inventory, []


def make_actual_inventory(paths: list[str], source_kind: str) -> tuple[dict, list[dict]]:
    normalized, findings = normalize_paths_with_findings(paths, "actual_path")
    entries = [
        {
            "path": path,
            "kind": "modified",
            "existence": True,
            "digest": None,
            "inventory_source": source_kind,
            "verified": True,
            "metadata": {},
        }
        for path in normalized
    ]
    return {
        "schema": "govern-project-docs.inventory.v1",
        "source": {"kind": source_kind, "verified": not findings},
        "entries": entries,
    }, findings


def git_status_inventory(workspace: Path) -> tuple[dict, list[dict]]:
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workspace,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return {
            "schema": "govern-project-docs.inventory.v1",
            "source": {"kind": "git", "verified": False},
            "entries": [],
        }, [{"code": "git-change-source-unavailable", "message": str(exc)}]

    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=workspace,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    fields = completed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    entries = []
    findings = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        status = record[:2]
        raw_path = record[3:]
        path, finding = normalize_path_value(raw_path, "git.path")
        if finding:
            findings.append(finding)
            continue
        if "R" in status or "C" in status:
            raw_old = fields[index] if index < len(fields) else ""
            index += 1
            old_path, old_finding = normalize_path_value(raw_old, "git.old_path")
            if old_finding:
                findings.append(old_finding)
                continue
            entries.append({
                "path": path,
                "kind": "renamed",
                "old_path": old_path,
                "new_path": path,
                "existence": True,
                "digest": None,
                "inventory_source": "git",
                "verified": True,
                "metadata": {"status": status},
            })
        elif "D" in status:
            entries.append({
                "path": path,
                "kind": "deleted",
                "existence": False,
                "digest": None,
                "inventory_source": "git",
                "verified": True,
                "metadata": {"status": status},
            })
        elif status == "??":
            entries.append({
                "path": path,
                "kind": "added",
                "existence": True,
                "digest": None,
                "inventory_source": "git",
                "verified": True,
                "metadata": {"status": status},
            })
        else:
            entries.append({
                "path": path,
                "kind": "modified",
                "existence": True,
                "digest": None,
                "inventory_source": "git",
                "verified": True,
                "metadata": {"status": status},
            })

    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=workspace,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "schema": "govern-project-docs.inventory.v1",
        "source": {
            "kind": "git",
            "verified": not findings,
            "metadata": {"head": head.stdout.strip() if head.returncode == 0 else None},
        },
        "entries": entries,
    }, findings


def git_changed_paths(workspace: Path) -> tuple[list[str], dict | None]:
    inventory, findings = git_status_inventory(workspace)
    if findings:
        return [], findings[0]
    return inventory_event_paths(inventory.get("entries", [])), None


def change_verification(
    workspace: Path,
    declared_paths: list[str],
    actual_paths: list[str],
    change_source: str,
    *,
    change_entries: list[dict] | None = None,
    event_isolation_verified: bool = False,
    source_metadata: dict | None = None,
) -> tuple[dict, list[dict]]:
    declared, declared_findings = normalize_paths_with_findings(declared_paths, "changed_path")
    requested_actual, actual_findings = normalize_paths_with_findings(actual_paths, "actual_path")
    findings = declared_findings + actual_findings

    if change_entries is not None:
        actual = inventory_event_paths(change_entries)
        source = change_source
        verified = True
    elif change_source == "supplied" or (change_source == "auto" and requested_actual):
        actual = requested_actual
        source = "supplied"
        verified = True
    elif change_source in {"git", "auto"}:
        actual, git_error = git_changed_paths(workspace)
        if git_error:
            if change_source == "git":
                findings.append(git_error)
                actual = []
                source = "git"
                verified = False
            else:
                actual = declared
                source = "explicit"
                verified = False
        else:
            source = "git"
            verified = True
    else:
        actual = declared
        source = "explicit"
        verified = False

    if verified:
        undeclared = sorted(set(actual) - set(declared))
        not_actual = sorted(set(declared) - set(actual))
        for path in undeclared:
            findings.append({"code": "actual-path-not-declared", "path": path, "source": source})
        for path in not_actual:
            findings.append({"code": "declared-path-not-actually-changed", "path": path, "source": source})

    return {
        "source": source,
        "verified": verified,
        "declared_paths": declared,
        "actual_paths": actual,
        "event_isolation_verified": event_isolation_verified,
        "source_metadata": source_metadata or {},
        "unverified_reason": None if verified else "actual changed paths were not independently verified",
    }, findings


def resolve_change_inventory(
    adapter: dict,
    workspace: Path,
    args: argparse.Namespace | None,
    *,
    changed_paths: list[str],
    actual_paths: list[str],
    change_source: str,
) -> tuple[list[dict] | None, list[str], dict | None, list[dict], bool, str, dict]:
    findings: list[dict] = []
    pre_existing_unchanged: list[str] = []
    receipt = None
    event_isolation_verified = False
    source_metadata: dict = {}
    extra_excluded: list[str] = []

    receipt_path = getattr(args, "receipt", None) if args else None
    if receipt_path:
        try:
            receipt_file = Path(receipt_path)
            receipt = load_json(receipt_file)
            try:
                extra_excluded.append(str(receipt_file.resolve().relative_to(workspace.resolve())))
            except ValueError:
                pass
        except FileNotFoundError:
            findings.append({"code": "receipt-missing", "path": receipt_path})

    baseline_inventory = None
    final_inventory = None
    if receipt and isinstance(receipt.get("baseline_inventory"), dict):
        baseline_inventory = receipt["baseline_inventory"]
        change_source = receipt.get("inventory_source", {}).get("kind", change_source)
        source_metadata.update(receipt.get("inventory_source", {}).get("metadata", {}))

    if args:
        loaded, load_findings = load_inventory_file(getattr(args, "baseline_inventory", None))
        findings.extend(load_findings)
        if loaded:
            baseline_inventory = loaded
            change_source = loaded.get("source", {}).get("kind", change_source)
        loaded, load_findings = load_inventory_file(getattr(args, "final_inventory", None))
        findings.extend(load_findings)
        if loaded:
            final_inventory = loaded

    if baseline_inventory and not final_inventory:
        if change_source == "filesystem":
            final_inventory, scan_findings = scan_filesystem_inventory(
                workspace,
                adapter,
                source_kind="filesystem",
                extra_excluded=extra_excluded,
            )
            findings.extend(scan_findings)
        elif change_source == "supplied":
            findings.append({"code": "missing-final-inventory", "source": "supplied"})

    if baseline_inventory and final_inventory:
        change_entries, pre_existing_unchanged = compare_inventories(baseline_inventory, final_inventory)
        event_isolation_verified = True
        source_metadata.update(final_inventory.get("source", {}).get("metadata", {}))
        return change_entries, pre_existing_unchanged, receipt, findings, event_isolation_verified, change_source, source_metadata

    if change_source == "filesystem":
        inventory, scan_findings = scan_filesystem_inventory(workspace, adapter, source_kind="filesystem")
        findings.extend(scan_findings)
        entries = inventory.get("entries", [])
        return entries, [], receipt, findings, False, "filesystem", inventory.get("source", {}).get("metadata", {})

    if change_source in {"git", "auto"}:
        inventory, git_findings = git_status_inventory(workspace)
        if not git_findings:
            entries = inventory.get("entries", [])
            return entries, [], receipt, findings, False, "git", inventory.get("source", {}).get("metadata", {})
        if change_source == "git":
            findings.extend(git_findings)
            return [], [], receipt, findings, False, "git", {}

    if change_source == "supplied" or actual_paths:
        inventory, actual_findings = make_actual_inventory(actual_paths, "supplied")
        findings.extend(actual_findings)
        entries = inventory.get("entries", [])
        return entries, [], receipt, findings, False, "supplied", {}

    return None, [], receipt, findings, False, "explicit", {}


def parse_protected_approvals(bindings: list[str]) -> tuple[list[dict], list[dict]]:
    approvals = []
    findings = []
    seen = set()
    for binding in bindings:
        if "=" not in binding:
            findings.append({"code": "invalid-protected-approval", "value": binding})
            continue
        path, evidence = (part.strip().rstrip("/") for part in binding.split("=", 1))
        if not path or not evidence:
            findings.append({"code": "invalid-protected-approval", "value": binding})
            continue
        if path in seen:
            findings.append({"code": "duplicate-protected-approval", "path": path})
            continue
        seen.add(path)
        approvals.append({"path": path, "evidence": evidence})
    return approvals, findings


def parse_human_approvals(bindings: list[str]) -> tuple[list[dict], list[dict]]:
    approvals = []
    findings = []
    seen = set()
    for binding in bindings:
        if "=" not in binding:
            findings.append({"code": "invalid-human-approval", "value": binding})
            continue
        approval_type, evidence = (part.strip().rstrip("/") for part in binding.split("=", 1))
        if not approval_type or not evidence:
            findings.append({"code": "invalid-human-approval", "value": binding})
            continue
        key = (approval_type, evidence)
        if key in seen:
            findings.append({"code": "duplicate-human-approval", "type": approval_type, "evidence": evidence})
            continue
        seen.add(key)
        approvals.append({"type": approval_type, "evidence": evidence})
    return approvals, findings


def text_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def path_scope_tokens(path: str) -> set[str]:
    words = text_words(Path(path).as_posix())
    return {word for word in words if len(word) >= 3 and not word.isdigit()}


def text_records_human_approval(text: str, approval_type: str, targets: list[str]) -> bool:
    words = text_words(text)
    if not {"human", "approval"}.issubset(words) and "approved" not in words:
        return False
    type_tokens = {word for word in text_words(approval_type) if len(word) >= 4}
    if type_tokens and not type_tokens.intersection(words):
        return False
    for target in targets:
        target_tokens = path_scope_tokens(target)
        if target not in text and target_tokens and not target_tokens.intersection(words):
            return False
    return True


def required_historical_approval_type(adapter: dict) -> str:
    declared = adapter.get("human_approval", [])
    if "irreversible archive handling" in declared:
        return "irreversible archive handling"
    if "historical material change" in declared:
        return "historical material change"
    return "historical material change"


SEMANTIC_ANSWER_KEYS = {
    "important_claims_changed",
    "affected_questions",
    "documents_agree_with_evidence",
    "remaining_uncertainty",
}

SEMANTIC_FINDING_KEYS = {
    "code",
    "affected_question",
    "evidence",
    "confidence",
    "decision_boundary",
    "suggested_handling",
    "human_boundary",
    "status",
}


def evaluate_semantic_review(
    review_path: str | None,
    *,
    required: bool,
    workspace: Path,
    event_paths: list[str],
    authorized_docs: list[str],
) -> tuple[dict, list[dict], list[dict], list[str]]:
    if not review_path:
        return {
            "status": "not supplied or not bound",
            "required": required,
            "findings": [],
        }, [], [], ["semantic review not supplied or not bound"] if required else []

    mechanical: list[dict] = []
    unverified: list[str] = []
    review_file = Path(review_path)
    try:
        review = load_json(review_file)
    except FileNotFoundError:
        return {
            "status": "missing",
            "required": required,
            "findings": [],
        }, [{"code": "semantic-review-missing", "path": review_path}], [], []
    except SystemExit:
        return {
            "status": "malformed",
            "required": required,
            "findings": [],
        }, [{"code": "malformed-semantic-review", "path": review_path}], [], []

    answers = review.get("answers")
    if not isinstance(answers, dict) or not SEMANTIC_ANSWER_KEYS.issubset(answers):
        mechanical.append({"code": "malformed-semantic-review", "path": review_path, "field": "answers"})
    findings = review.get("findings")
    if not isinstance(findings, list):
        mechanical.append({"code": "malformed-semantic-review", "path": review_path, "field": "findings"})
        findings = []

    normalized_event = set(event_paths)
    normalized_authorized, auth_findings = normalize_paths_with_findings(authorized_docs, "authorized_doc")
    mechanical.extend(auth_findings)
    semantic_findings = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict) or not SEMANTIC_FINDING_KEYS.issubset(finding):
            mechanical.append({"code": "malformed-semantic-review", "path": review_path, "finding": index})
            continue
        semantic_findings.append(finding)
        status = finding.get("status")
        if status == "unresolved":
            unverified.append("unresolved-semantic-finding")
            continue
        if status != "resolved":
            mechanical.append({"code": "malformed-semantic-review", "path": review_path, "finding": index, "field": "status"})
            continue
        if not finding.get("resolution") or not finding.get("resolution_evidence"):
            unverified.append("semantic-resolution-evidence-missing")
            continue
        evidence, evidence_finding = normalize_path_value(str(finding.get("resolution_evidence")), "semantic_resolution_evidence")
        if evidence_finding:
            mechanical.append(evidence_finding)
            continue
        evidence_authorized = any(path_matches(evidence, pattern) for pattern in normalized_authorized)
        if evidence not in normalized_event or not evidence_authorized:
            unverified.append("semantic-resolution-evidence-missing")

    status = "bound" if not mechanical and not unverified else "unresolved"
    return {
        "status": status,
        "required": required,
        "source": str(review_path),
        "answers": answers if isinstance(answers, dict) else {},
        "findings": semantic_findings,
    }, mechanical, semantic_findings, sorted(set(unverified))


def live_closeout(
    adapter: dict,
    workspace: Path,
    changed_paths: list[str],
    actual_paths: list[str],
    change_source: str,
    authorized_docs: list[str],
    protected_approval_bindings: list[str],
    human_approval_bindings: list[str],
    args: argparse.Namespace | None = None,
) -> dict:
    adapter_result = validate_adapter(adapter)
    mechanical = list(adapter_result["mechanical_findings"])
    human_required = []
    boundary_rules = safe_section(adapter, "boundaries")
    entrypoint_rules = safe_section(adapter, "entrypoints")
    protected_patterns = boundary_rules.get("protected", [])
    excluded_patterns = boundary_rules.get("excluded", [])
    ordinary_patterns = boundary_rules.get("ordinary_docs", [])
    historical_patterns = entrypoint_rules.get("historical", [])
    change_entries, pre_existing_unchanged, receipt, inventory_findings, event_isolation_verified, resolved_source, source_metadata = resolve_change_inventory(
        adapter,
        workspace,
        args,
        changed_paths=changed_paths,
        actual_paths=actual_paths,
        change_source=change_source,
    )
    mechanical.extend(inventory_findings)
    verification, verification_findings = change_verification(
        workspace,
        changed_paths,
        actual_paths,
        resolved_source,
        change_entries=change_entries,
        event_isolation_verified=event_isolation_verified,
        source_metadata=source_metadata,
    )
    mechanical.extend(verification_findings)
    approvals, approval_findings = parse_protected_approvals(protected_approval_bindings)
    human_approvals, human_approval_findings = parse_human_approvals(human_approval_bindings)
    mechanical.extend(approval_findings)
    mechanical.extend(human_approval_findings)
    normalized_changed = set(verification["actual_paths"] if verification["verified"] else verification["declared_paths"])
    event_paths = sorted(normalized_changed)
    declared_human_types = adapter.get("human_approval", []) if is_string_list(adapter.get("human_approval")) else []
    valid_approvals = []
    valid_approval_paths = set()
    valid_human_approvals = []
    valid_human_targets = set()

    changed_historical_paths = [
        changed
        for changed in event_paths
        if any(path_matches(changed, pattern) for pattern in historical_patterns)
    ]
    dirty_changed_paths = sorted({
        path
        for entry in change_entries or []
        if (entry.get("metadata") or {}).get("dirty_at_baseline")
        for path in inventory_entry_paths(entry)
    })
    semantic_review, semantic_mechanical, bound_semantic_findings, semantic_unverified = evaluate_semantic_review(
        getattr(args, "semantic_review", None) if args else None,
        required=bool(getattr(args, "require_semantic_review", False)) if args else False,
        workspace=workspace,
        event_paths=event_paths,
        authorized_docs=authorized_docs,
    )
    mechanical.extend(semantic_mechanical)

    for approval in approvals:
        path = approval["path"]
        evidence = approval["evidence"]
        before = len(mechanical)
        path_protected = any(path_matches(path, pattern) for pattern in protected_patterns)
        path_excluded = any(path_matches(path, pattern) for pattern in excluded_patterns)
        path_historical = any(path_matches(path, pattern) for pattern in historical_patterns)
        path_authorized = any(path_matches(path, pattern) for pattern in authorized_docs)
        evidence_authorized = any(path_matches(evidence, pattern) for pattern in authorized_docs)
        evidence_ordinary = any(path_matches(evidence, pattern) for pattern in ordinary_patterns)
        evidence_protected = any(path_matches(evidence, pattern) for pattern in protected_patterns)
        evidence_excluded = any(path_matches(evidence, pattern) for pattern in excluded_patterns)
        evidence_historical = any(path_matches(evidence, pattern) for pattern in historical_patterns)

        if path not in normalized_changed:
            mechanical.append({"code": "protected-approval-path-not-changed", "path": path})
        if not path_protected:
            mechanical.append({"code": "protected-approval-path-not-protected", "path": path})
        if not path_authorized:
            mechanical.append({"code": "protected-approval-path-not-authorized", "path": path})
        if path_excluded:
            mechanical.append({"code": "protected-approval-path-excluded", "path": path})
        if path_historical:
            mechanical.append({"code": "protected-approval-path-historical", "path": path})

        if evidence not in normalized_changed or not evidence_authorized:
            mechanical.append({
                "code": "protected-approval-evidence-not-in-event",
                "path": path,
                "evidence": evidence,
            })
        if not evidence_ordinary or evidence_protected or evidence_excluded or evidence_historical:
            mechanical.append({
                "code": "invalid-protected-approval-evidence-boundary",
                "path": path,
                "evidence": evidence,
            })

        evidence_path = (workspace / evidence).resolve()
        try:
            evidence_path.relative_to(workspace.resolve())
        except ValueError:
            mechanical.append({
                "code": "protected-approval-evidence-outside-workspace",
                "path": path,
                "evidence": evidence,
            })
        else:
            if not evidence_path.is_file():
                mechanical.append({
                    "code": "missing-protected-approval-evidence",
                    "path": path,
                    "evidence": evidence,
                })

        if len(mechanical) == before:
            valid_approvals.append(approval)
            valid_approval_paths.add(path)

    for approval in human_approvals:
        approval_type = approval["type"]
        evidence = approval["evidence"]
        before = len(mechanical)

        if approval_type not in declared_human_types:
            mechanical.append({"code": "human-approval-type-not-declared", "type": approval_type})

        evidence_authorized = any(path_matches(evidence, pattern) for pattern in authorized_docs)
        evidence_ordinary = any(path_matches(evidence, pattern) for pattern in ordinary_patterns)
        evidence_protected = any(path_matches(evidence, pattern) for pattern in protected_patterns)
        evidence_excluded = any(path_matches(evidence, pattern) for pattern in excluded_patterns)
        evidence_historical = any(path_matches(evidence, pattern) for pattern in historical_patterns)

        if evidence not in normalized_changed:
            mechanical.append({
                "code": "human-approval-evidence-not-in-event",
                "type": approval_type,
                "evidence": evidence,
            })
        if not evidence_authorized:
            mechanical.append({
                "code": "human-approval-evidence-not-authorized",
                "type": approval_type,
                "evidence": evidence,
            })
        if not evidence_ordinary or evidence_protected or evidence_excluded or evidence_historical:
            mechanical.append({
                "code": "invalid-human-approval-evidence-boundary",
                "type": approval_type,
                "evidence": evidence,
            })

        evidence_path = (workspace / evidence).resolve()
        try:
            evidence_path.relative_to(workspace.resolve())
        except ValueError:
            mechanical.append({
                "code": "human-approval-evidence-outside-workspace",
                "type": approval_type,
                "evidence": evidence,
            })
            evidence_text = ""
        else:
            if not evidence_path.is_file():
                mechanical.append({
                    "code": "missing-human-approval-evidence",
                    "type": approval_type,
                    "evidence": evidence,
                })
                evidence_text = ""
            else:
                evidence_text = evidence_path.read_text(encoding="utf-8")

        if changed_historical_paths:
            authorized_targets = [
                path
                for path in changed_historical_paths
                if any(path_matches(path, pattern) for pattern in authorized_docs)
            ]
        else:
            authorized_targets = []
        if changed_historical_paths and sorted(authorized_targets) != sorted(changed_historical_paths):
            mechanical.append({
                "code": "human-approval-target-not-authorized",
                "type": approval_type,
                "targets": sorted(set(changed_historical_paths) - set(authorized_targets)),
            })

        if changed_historical_paths and evidence_text and not text_records_human_approval(
            evidence_text,
            approval_type,
            changed_historical_paths,
        ):
            mechanical.append({
                "code": "human-approval-scope-mismatch",
                "type": approval_type,
                "evidence": evidence,
                "targets": changed_historical_paths,
            })

        if len(mechanical) == before:
            accepted = {
                "type": approval_type,
                "evidence": evidence,
                "targets": changed_historical_paths,
            }
            valid_human_approvals.append(accepted)
            valid_human_targets.update(changed_historical_paths)

    for changed in event_paths:
        changed_protected = [pattern for pattern in protected_patterns if path_matches(changed, pattern)]
        changed_excluded = [pattern for pattern in excluded_patterns if path_matches(changed, pattern)]
        changed_historical = [pattern for pattern in historical_patterns if path_matches(changed, pattern)]
        changed_ordinary = [pattern for pattern in ordinary_patterns if path_matches(changed, pattern)]
        authorized = any(path_matches(changed, pattern) for pattern in authorized_docs)

        normalized = changed.rstrip("/")

        if changed_excluded:
            mechanical.append({
                "code": "excluded-path-changed",
                "path": changed,
                "matched": changed_excluded,
            })
            continue
        if changed_protected and normalized not in valid_approval_paths:
            mechanical.append({
                "code": "protected-path-changed",
                "path": changed,
                "matched": changed_protected,
            })
            continue
        if changed_protected:
            continue
        if changed_historical:
            if not authorized:
                mechanical.append({
                    "code": "unauthorized-historical-change",
                    "path": changed,
                    "matched": changed_historical,
                })
            elif normalized not in valid_human_targets:
                human_required.append(required_historical_approval_type(adapter))
            continue
        if changed_ordinary and not authorized:
            mechanical.append({
                "code": "unauthorized-ordinary-doc-change",
                "path": changed,
                "matched": changed_ordinary,
            })
            continue
        if not authorized:
            mechanical.append({"code": "unauthorized-change", "path": changed})

    if mechanical:
        result = "fail"
    elif human_required:
        result = "unproven"
    elif not verification["verified"]:
        result = "unproven"
    elif dirty_changed_paths:
        result = "unproven"
    elif semantic_unverified:
        result = "unproven"
    else:
        result = "pass"

    unverified = []
    if not verification["verified"]:
        unverified.append("actual-change-set")
    if not verification["event_isolation_verified"]:
        unverified.append("event-isolation")
    if dirty_changed_paths:
        unverified.append("dirty-baseline-attribution-unproven")
    unverified.extend(semantic_unverified)

    return {
        "result": result,
        "coverage": {
            "inventory_source": verification["source"],
            "actual_change_set_verified": verification["verified"],
            "baseline_receipt_used": bool(receipt),
            "event_isolation_verified": verification["event_isolation_verified"],
            "semantic_review_bound": semantic_review["status"] == "bound",
            "human_boundary_complete": not bool(human_required),
            "unverified": sorted(set(unverified)),
            "cannot_prove": [
                "which AI or human actor modified a file",
                "semantic truth of document claims",
            ],
        },
        "closeout": {
            "mode": "live",
            "workspace": str(workspace),
            "changed_paths": verification["declared_paths"],
            "actual_paths": verification["actual_paths"],
            "change_inventory": change_entries or [],
            "pre_existing_unchanged": pre_existing_unchanged,
            "change_verification": verification,
            "authorized_docs": authorized_docs,
            "protected_approvals": valid_approvals,
            "verified_human_approvals": valid_human_approvals,
        },
        "mechanical_findings": mechanical,
        "semantic_review": semantic_review,
        "semantic_findings": bound_semantic_findings,
        "human_approval_required": sorted(set(human_required)),
        "recovery": "Live Closeout checked adapter, receipt/baseline, change source, actual event paths, affected governed questions, unresolved semantic findings, unresolved human boundaries, and next inputs needed for recovery.",
    }


def closeout_command(args: argparse.Namespace) -> None:
    live_requested = bool(args.changed_path or args.receipt or args.baseline_inventory or args.final_inventory or args.actual_path)
    if live_requested:
        adapter, missing = load_json_or_missing(Path(args.adapter))
        if missing:
            missing["closeout"] = {
                "mode": "live",
                "workspace": args.workspace,
                "changed_paths": args.changed_path,
                "actual_paths": args.actual_path,
                "authorized_docs": args.authorized_doc,
                "protected_approvals": [],
                "verified_human_approvals": [],
            }
            missing["semantic_review"] = {
                "status": "not supplied or not bound",
                "required": bool(args.require_semantic_review),
                "findings": [],
            }
            emit(missing)
            return
        payload = live_closeout(
            adapter,
            Path(args.workspace),
            args.changed_path,
            args.actual_path,
            args.change_source,
            args.authorized_doc,
            args.protected_approval,
            args.human_approval,
            args,
        )
    else:
        if not args.cases:
            raise SystemExit("closeout requires either --changed-path for live mode or a cases file for fixture mode")
        payload = run_cases(args)
        payload["closeout"] = {
            "mode": "fixture",
            "adapter": args.adapter,
            "cases": args.cases,
            "workspace": args.workspace,
        }
    emit(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="govern-project-docs deterministic checker")
    sub = parser.add_subparsers(required=True)

    validate = sub.add_parser("validate-adapter")
    validate.add_argument("adapter")
    validate.set_defaults(func=validate_adapter_command)

    impact = sub.add_parser("impact")
    impact.add_argument("adapter")
    impact.add_argument("--changed-path", action="append", default=[])
    impact.add_argument("--workspace")
    impact.add_argument(
        "--change-source",
        choices=["auto", "git", "filesystem", "supplied", "explicit"],
        default="auto",
    )
    impact.set_defaults(func=impact_command)

    run = sub.add_parser("run-cases")
    run.add_argument("adapter")
    run.add_argument("cases")
    run.add_argument("--workspace", required=True)
    run.set_defaults(func=run_cases_command)

    closeout = sub.add_parser("closeout")
    closeout.add_argument("adapter")
    closeout.add_argument("cases", nargs="?")
    closeout.add_argument("--workspace", required=True)
    closeout.add_argument("--changed-path", action="append", default=[])
    closeout.add_argument("--actual-path", action="append", default=[])
    closeout.add_argument(
        "--change-source",
        choices=["auto", "git", "filesystem", "supplied", "explicit"],
        default="auto",
    )
    closeout.add_argument("--receipt")
    closeout.add_argument("--baseline-inventory")
    closeout.add_argument("--final-inventory")
    closeout.add_argument("--authorized-doc", action="append", default=[])
    closeout.add_argument("--semantic-review")
    closeout.add_argument("--require-semantic-review", action="store_true")
    closeout.add_argument(
        "--protected-approval",
        action="append",
        default=[],
        metavar="PATH=EVIDENCE",
    )
    closeout.add_argument(
        "--human-approval",
        action="append",
        default=[],
        metavar="TYPE=EVIDENCE",
    )
    closeout.set_defaults(func=closeout_command)

    diagnose_parser = sub.add_parser("diagnose")
    diagnose_parser.add_argument("adapter")
    diagnose_parser.add_argument("--workspace", required=True)
    diagnose_parser.set_defaults(func=diagnose_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
