---
name: govern-project-docs
description: Use when a development task may change project facts, plans, decisions, evidence, release claims, or historical interpretation and needs documentation Impact or Closeout governance.
---

# govern-project-docs

Keep project documents aligned with development reality through a small loop:
resolve authority, run Impact before work, run Closeout before completion, and
separate mechanical checks from AI semantic review and human decisions.

## Core Rule

Govern by `(question, scope)`, not by filename. A project may use any document
structure if each governed question resolves through one rule or an explicitly
ordered set of sources.

Do not treat Skill output as project authority. Findings are evidence until the
durable conclusion is written back into mapped project documents.

## Inputs

Use a project adapter with only pointers, rules, and boundaries. Do not copy
current project facts into the adapter.

Read `references/adapter-schema.md` when creating or validating an adapter.
Use `scripts/govern_project_docs.py` for deterministic adapter, fixture, and
live diagnostic checks.

## Essential Workflow

Use one order for a governed development event:

`Impact → authorized edits → semantic review → freeze → project validation → Closeout → no governed edits`

Impact records the pre-change baseline. Freeze fingerprints the final event
paths. Project-selected validation runs against those exact bytes. Closeout
binds the baseline, final paths, semantic review, approvals, and freeze receipt.
Any governed edit after freeze invalidates the proof: refreeze, rerun
project-selected affected validation, and rerun Closeout.

Do not write promises such as “Closeout will run later” into persistent project
facts. Run the gate in the task that is closing. Impact, freeze, and Closeout
receipts are generated, non-authoritative evidence; keep them outside governed
authority or in an adapter-excluded location, and do not commit them unless the
project explicitly chooses to.

## Validation Reuse And Invalidation

After any governed edit made after Freeze, create a new Freeze, run the
project-selected validation affected by that edit, and rerun Closeout. This does
not default to rerunning all tests.

Reuse validation evidence while its relevant inputs, command, configuration,
environment, and supported claim are unchanged. A status update, gate rename,
or Closeout retry does not by itself invalidate unrelated evidence.

- Ordinary governance-document edits normally invalidate only the structural,
  link, documentation, or architecture checks that consume them. If the
  project validation contract treats a document as a tested current-fact or
  executable input, rerun every obligation that consumes it.
- Runtime, public API, persistence, routing, architecture-contract, evaluation,
  or other full-regression input changes invalidate the corresponding complete
  validation required by the project contract.
- For `unproven`, follow `recovery_actions`; do not repeat unrelated tests to
  compensate for missing event isolation, approval mapping, or a human decision.
- When several validation layers require the same obligation, execute the
  deduplicated union only when command, inputs, configuration, environment, and
  claim scope are materially identical.

## Impact

Run Impact before work starts or when the task meaning changes.

For a live event, use `--workspace` and preserve the emitted receipt outside
project authority. An empty Impact scope is `unproven`, not a successful no-op.

Return:

- affected governed questions and authority rules;
- evidence entrypoints to inspect;
- protected or excluded paths touched;
- candidate document authorities likely to need update;
- human approval boundaries.

Impact result:

- `pass` when the work has mapped authority and no known human or protected
  boundary;
- `unproven` when the adapter is missing, authority, evidence, or approval is
  missing or uncertain, or excluded paths are touched;
- `fail` only for directly provable structural defects.

## Closeout

Run Closeout before declaring a task, batch, decision, validation change, or
release-stage transition complete.

For live Codex work, pass the declared changed paths, the documents authorized
for the event, the Impact receipt, and a final-content freeze receipt. Closeout
consumes a common Change Inventory, not raw Git semantics. Git is optional:
filesystem snapshots provide the same event-isolation contract. Supplied or
explicit path-only modes remain `unproven`. Fixture-only Closeout is for
regression cases, not live task approval.

Create the freeze after all governed edits and semantic dispositions, then run
the project's own validation:

```bash
python3 scripts/govern_project_docs.py freeze adapter.json \
  --workspace /path/to/project \
  --changed-path STATUS.md \
  --write-receipt /tmp/project-freeze.json
```

Protected paths fail by default. When a human has explicitly approved a
protected configuration change, bind each approved path to a durable ordinary
document changed and authorized in the same event with
`--protected-approval PATH=EVIDENCE`. The evidence document must record the
approval scope. Never use this mechanism for excluded, generated, historical,
or otherwise unauthorized paths.

When a human has explicitly approved a governed semantic boundary such as
historical material change, bind the approval type to an in-event ordinary
evidence document with `--human-approval "TYPE=EVIDENCE"`. `TYPE` must be
declared in the project adapter and mapped by the affected authority rule's
`human_approval_types`. The evidence must contain `Approval type:`, `Object:`,
`Scope:`, and `Does not approve:`. One exact type never satisfies another; a
protected-path approval never satisfies a semantic approval. The checker
verifies the binding, not the truth or identity of the human decision.

Return one result:

- `pass`: mapped authorities, evidence, and allowed document changes agree;
- `fail`: deterministic defects remain;
- `unproven`: evidence or authority is insufficient, or a human decision is
  required.

When semantic review is required, bind it with `--semantic-review REVIEW.json`.
Missing, unresolved, or unhandled semantic findings keep Closeout `unproven`;
malformed review input fails mechanically.

Closeout must include recovery information for the next AI task.
Use `result_reasons`, `recovery_actions`, and `approval_summary` for automation;
the legacy `recovery` sentence remains for compatibility.

## Live Diagnostic

Run `diagnose` for a read-only Codex diagnostic of a real workspace. It checks
adapter structure, mapped authority targets, current and evidence entrypoints,
configured plan-status conflicts, and local Markdown links in current authority
documents only.

Do not treat diagnostic output as project authority. Write durable conclusions
back into mapped documents only when the event authorizes documentation edits.

## Mechanical Checks

Mechanical findings may block only directly provable defects:

- invalid adapter JSON;
- missing project adapter;
- missing required adapter sections or wrong basic types;
- duplicate authority rule id;
- missing mapped target or evidence entrypoint;
- broken active link when checked by the project;
- current/evidence entrypoints missing from the workspace;
- plan files marked active while current state says no active batch;
- historical material configured as current;
- generated result presented as authority;
- actual changed path not declared, or declared path not actually changed when
  actual-path verification is available;
- protected or excluded path changed in a closeout.

The only exception to a protected-path failure is a valid path-scoped approval
binding backed by an in-event evidence document. Missing, mismatched, external,
or out-of-event evidence fails mechanically; excluded paths remain blocked.

Valid human approval bindings are reported separately as
`verified_human_approvals`. They may satisfy a required human boundary, but
they are not mechanical proof of the product or architecture decision itself.

Live `pass` requires event isolation and an unchanged final-content freeze.
No supported collector proves which human or AI actor changed a file; Closeout
reports that limitation without downgrading an otherwise complete result.

## AI Semantic Review

Ask four questions:

1. What important claims changed?
2. Which governed questions are affected?
3. Do current documents agree with available evidence?
4. What remains uncertain?

Each semantic finding must include evidence, confidence, suggested handling, and
whether a human decision is required. Semantic findings are not deterministic
failures unless the project has promoted the condition to a repeatable check.

Required semantic finding fields:

- `code`
- `affected_question`
- `evidence`
- `confidence`
- `decision_boundary`
- `suggested_handling`
- `human_boundary`

## Human Approval

Ask before changing:

- authority assignment;
- product or architecture meaning;
- formal release or version claims;
- deletion, significant supersession, or irreversible archive handling.

## Codex Runtime Target

This Skill only needs to run reliably in Codex for V1. Do not add CI or other
platform machinery unless a later task explicitly expands the scope.
