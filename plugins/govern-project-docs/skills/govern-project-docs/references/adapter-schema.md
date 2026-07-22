# Adapter And Result Contract

The adapter is JSON in V1. It stores pointers and rules, not current project
facts.

## Adapter Fields

- `schema_version`: string, currently `"1"`.
- `project`: project or fixture identifier.
- `authority_rules`: list of rules.
- `entrypoints.current`: current authority entrypoints.
- `entrypoints.historical`: historical evidence entrypoints.
- `entrypoints.evidence`: validation and evidence entrypoints.
- `boundaries.protected`: paths that may be read as evidence but not modified
  by documentation governance.
- `boundaries.excluded`: paths ignored by default.
- `boundaries.ordinary_docs`: documentation candidates that still need event
  authorization before edits.
- `human_approval`: human decision categories.
- `plan_status_checks`: optional consistency checks between a current status
  document and mapped plan files.

`entrypoints`, `boundaries`, and `human_approval` are required. `current` and
`evidence` entrypoints must be non-empty lists. All path lists contain strings.

## Authority Rule

Each authority rule contains:

- `id`: project-local stable handle.
- `question`: governed question.
- `scope`: scope string.
- `paths`: ordered source pointers.
- `protected`: optional boolean.
- `human`: optional boolean.
- `triggers`: optional list of implementation, test, config, or evidence path
  prefixes that may affect this governed question.
- `human_approval_types`: optional list of precise approval categories declared
  in top-level `human_approval`.

Rule ids must be unique. Ordered `paths` express precedence without copying
facts. `triggers` route Impact only; they never authorize protected source,
test, config, or generated writes.

## Result

Every run returns:

- `result`: `pass`, `fail`, or `unproven`;
- `mechanical_findings`;
- `semantic_findings`;
- `human_approval_required`;
- `coverage`;
- `recovery`.

`fail` is reserved for deterministic defects. Semantic uncertainty or missing
human approval is `unproven`.

Missing adapter files return `unproven` with `adapter-missing` rather than a
traceback. Create a candidate adapter from project pointers and ask for human
approval before treating it as project governance authority.

## Impact

`impact <adapter> --changed-path <path>` first validates the adapter. Invalid
adapters return `fail` and do not produce a passable Impact. Valid Impact output
includes affected authority rules, candidate authority paths, protected paths,
excluded paths, and evidence entrypoints. `paths` and optional `triggers` both
route changes to governed questions.

With `--workspace`, Impact emits a receipt:

- `schema`: receipt schema id;
- adapter/project identity;
- workspace identity;
- inventory source kind and metadata;
- baseline inventory or stable summary;
- planned paths;
- affected governed questions;
- candidate authority paths;
- protected/excluded/human boundaries;
- verification capability;
- recovery instructions.

The receipt is derived evidence, not project authority. Pass it to Closeout with
`--receipt` when you need filesystem or Git baseline isolation.

## Live Diagnostic

`diagnose <adapter> --workspace <path>` checks:

- adapter structure;
- mapped authority targets;
- current entrypoints;
- evidence entrypoints;
- configured plan-status conflicts;
- local Markdown links in current authority documents.

It does not crawl the whole repository.

## Live Closeout

Use live mode for real Codex tasks:

```bash
scripts/govern_project_docs.py closeout adapter.json \
  --workspace /path/to/project \
  --changed-path STATUS.md \
  --authorized-doc STATUS.md
```

The command fails protected, excluded, unauthorized ordinary-document, and
unauthorized non-document writes. Authorized historical writes remain
`unproven` when the adapter requires human approval for archive handling.

Closeout consumes a common Change Inventory:

- `path`;
- `kind`: `added`, `modified`, `deleted`, or `renamed`;
- optional `old_path` and `new_path`;
- `existence`;
- digest or other stable fingerprint when available;
- inventory source;
- `verified`;
- source-specific metadata.

Supported sources:

- `--change-source git`: compare declarations against the Git working tree;
- `--change-source filesystem` plus `--receipt`: compare a filesystem baseline
  with a final filesystem snapshot;
- `--change-source supplied --baseline-inventory A --final-inventory B`:
  compare externally supplied inventories;
- `--change-source supplied --actual-path PATH`: compare declarations against a
  verified final actual path list only;
- `--change-source explicit`: trust only declared paths and return `unproven`
  if no deterministic defect exists.

The default `auto` mode uses supplied `--actual-path` values when present,
then Git when available, then filesystem snapshot when a receipt is supplied,
then explicit fallback. When actual verification is available, undeclared actual
paths and declared-but-unchanged paths fail mechanically.

Filesystem snapshot mode does not require Git. It scans inside the workspace,
skips adapter-declared excluded paths, preserves hidden directories such as
`.venv/` and `.github/`, rejects absolute or `..` escaping paths, and rejects
symlink targets outside the workspace. Rename detection is not attempted without
source support; unrecognized renames are treated as deleted plus added.

Git is an optional inventory collector. It uses NUL-delimited porcelain status
and reports staged, unstaged, untracked, deleted, and renamed paths. Git metadata
such as HEAD is source-specific metadata only; core governance checks must not
require it.

Supplied actual paths prove final path scope only. Supplied baseline plus final
inventory, filesystem receipt, or another baseline-capable source is needed to
verify event isolation. No mode proves which actor changed a file.

### Approved Protected Changes

Protected paths remain fail-by-default. For an explicitly human-approved
protected configuration change, bind each protected path to one durable evidence
document:

```bash
scripts/govern_project_docs.py closeout adapter.json \
  --workspace /path/to/project \
  --changed-path AGENTS.md \
  --changed-path reports/approval.md \
  --authorized-doc AGENTS.md \
  --authorized-doc reports/approval.md \
  --protected-approval AGENTS.md=reports/approval.md
```

The binding is valid only when:

- the protected path is an exact actual changed path and is event-authorized;
- the evidence is an ordinary document changed and authorized in the same
  event;
- the evidence file exists inside the workspace; and
- neither target nor evidence is excluded or historical.

The evidence document must state the human approval, protected paths, event
scope, and claim boundary. AI semantic review confirms that content; the
deterministic checker confirms path, event, and boundary integrity.

`closeout.protected_approvals` records accepted path-to-evidence bindings.
Malformed, duplicate, missing, mismatched, external, or out-of-event evidence
fails mechanically. An excluded path cannot be approved.

### Approved Human Boundaries

For a human-approved semantic boundary, bind the adapter-declared approval type
to one durable evidence document:

```bash
scripts/govern_project_docs.py closeout adapter.json \
  --workspace /path/to/project \
  --changed-path archive/resolved-question.md \
  --changed-path docs/decision.md \
  --authorized-doc archive/resolved-question.md \
  --authorized-doc docs/decision.md \
  --human-approval "historical material change=docs/decision.md"
```

The binding is valid only when:

- the approval type exists in `human_approval`;
- the evidence is inside the workspace;
- the evidence is an ordinary, non-protected, non-excluded, non-historical
  document;
- the evidence is actually changed and event-authorized;
- the target historical paths are actually changed and event-authorized; and
- the evidence records human approval plus the approval type, object, and
  scope in text.

Valid bindings appear in `closeout.verified_human_approvals`. They can satisfy
the Closeout gate for the declared human boundary, but they do not turn the
human decision into a mechanical fact.

### Semantic Review Binding

When a task requires AI semantic review, pass a review document:

```bash
scripts/govern_project_docs.py closeout adapter.json \
  --workspace /path/to/project \
  --receipt receipt.json \
  --changed-path STATUS.md \
  --authorized-doc STATUS.md \
  --require-semantic-review \
  --semantic-review review.json
```

The review is JSON in V1. It must include:

- four answers: `important_claims_changed`, `affected_questions`,
  `documents_agree_with_evidence`, and `remaining_uncertainty`;
- `findings`;
- each finding must include the seven semantic finding fields plus `status`;
- resolved findings require `resolution` and `resolution_evidence`.

Closeout behavior:

- missing required review: `unproven`;
- malformed review: `fail`;
- unresolved finding: `unproven`;
- resolved finding without event-authorized resolution evidence: `unproven`;
- all resolved findings with valid evidence may pass when mechanical and human
  boundaries also pass.

Mechanical checks validate structure, paths, and disposition completeness only.
They do not judge the truth of the semantic content.

## Semantic Finding Contract

Every semantic finding contains:

- `code`;
- `affected_question`;
- `evidence`;
- `confidence`;
- `decision_boundary`;
- `suggested_handling`;
- `human_boundary`.
