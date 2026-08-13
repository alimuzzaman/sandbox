# Quickstart: Remote-first development and tests

This quickstart describes the intended behavior after implementation. A remote must
already be registered and provisioned; no host is created implicitly.

## 1. Configure the project

Add the runtime policy to `sandbox.config.json` alongside the existing project kind and
runtime descriptor:

```json
{
  "runtime": {
    "default": "remote",
    "remote": "scaleway-sandbox",
    "workspace": "default",
    "executionProfile": "unit",
    "outputProfile": "smart",
    "executionProfiles": {
      "unit": {
        "timeoutSeconds": 1800,
        "stallSeconds": 300,
        "cancelGraceSeconds": 20,
        "cancelOnStall": false,
        "cleanup": "retain"
      },
      "e2e": {
        "timeoutSeconds": 14400,
        "stallSeconds": 900,
        "cancelGraceSeconds": 60,
        "cancelOnStall": false,
        "cleanup": "retain"
      }
    },
    "outputProfiles": {
      "agent-compact": {
        "mode": "smart",
        "everyLines": 20,
        "include": ["FAIL", "ERROR", "warning"],
        "before": 2,
        "after": 5,
        "deduplicate": true,
        "heartbeatSeconds": 30,
        "maxBytes": 65536
      }
    },
    "workspaces": {
      "default": {"persistent": true, "allowParallelSafe": false}
    },
    "testPlans": {
      "node-unit": {
        "executionProfile": "unit",
        "steps": [
          {"id": "unit", "argv": ["npm", "test"]}
        ]
      }
    }
  },
  "tests": {
    "modes": {
      "node-unit": {
        "argv": ["npm", "test"]
      }
    }
  }
}
```

Project configuration recommends remote execution. `--local` remains a visible,
explicit override.

## 2. Create or reuse a development workspace

```bash
./sb workspace create --remote scaleway-sandbox --workspace node-unit --ensure
./sb workspace status --remote scaleway-sandbox --workspace node-unit
```

The named workspace persists and is reused. Ordinary commands targeting it serialize
by default.

## 3. Run a Node test remotely

```bash
./sb test --remote scaleway-sandbox --workspace node-unit \
  --timeout 1800 --output-profile smart node-unit
```

Sandbox first deploys the exact local working tree, including supported uncommitted and
untracked files. It then returns a durable job ID. The remote supervisor owns the test
and logs; losing the local terminal or internet connection does not stop it.

To submit without following:

```bash
./sb test --remote scaleway-sandbox --workspace node-unit \
  --timeout 1800 --output-profile quiet node-unit
```

Remote test submission is detached by design and returns a durable job ID.

## 4. Inspect and resume

```bash
./sb job-status JOB_ID --remote scaleway-sandbox
./sb job-output JOB_ID --remote scaleway-sandbox \
  --follow --profile agent-compact
./sb job-output JOB_ID --remote scaleway-sandbox --profile full
./sb job-metrics JOB_ID --remote scaleway-sandbox
```

`status` shows lifecycle and health separately. A quiet process with continuing resource
activity is not reported as failed. A stall warning includes the evidence and threshold.

If follow disconnects, rerun it with the last returned cursor. The process was never
attached to that SSH/MCP response.

## 5. Retrieve full retained output and artifacts

```bash
./sb job-output JOB_ID --remote scaleway-sandbox \
  --stream stderr --profile full --tail-bytes 262144

./sb job-artifacts JOB_ID --remote scaleway-sandbox
./sb job-artifact-get JOB_ID ARTIFACT_ID --remote scaleway-sandbox \
  --output-file tmp/test-report.zip
```

Quiet/smart/sampled presentation never removes retained output. Artifact requests are
restricted to the deployed workspace and identified by artifact ID after collection.

## 6. Cancel a genuinely stuck job

```bash
./sb job-cancel JOB_ID --remote scaleway-sandbox
```

Graceful cancellation is default. Force cancellation is explicit:

```bash
./sb job-cancel JOB_ID --remote scaleway-sandbox --force
```

Sandbox verifies process identity before signaling the owned process group and retains
output produced before cancellation.

## 7. Run multiple tests in one workspace

Submitting two ordinary tests to `node-unit` queues the second behind the first.

If immediate concurrent execution is required for a mutable test, create a different
workspace explicitly instead of overriding safety:

```bash
./sb workspace create --remote scaleway-sandbox --workspace node-unit-2 --ensure
```

## 8. Run a matrix on isolated workspaces

```bash
./sb test matrix --remote scaleway-sandbox --plan php-matrix \
  --timeout 7200 --output-profile smart
```

The parent job reports aggregate status. Every cell has a deterministic isolated label,
separate instance, job ID, output, deadline, artifacts, and cleanup result. Excess cells
queue at the plan and host capacity. Configure `maxParallel` and each step's cleanup
policy in the declared plan. Failed cells remain inspectable unless policy explicitly
removes them.

## 9. Run compatible remote CI

Always preflight first when evaluating a workflow:

```bash
./sb ci preflight .github/workflows/test.yml \
  --remote scaleway-sandbox
```

If no unaccepted compatibility difference remains:

```bash
./sb ci run .github/workflows/test.yml \
  --remote scaleway-sandbox \
  --timeout 14400 --output-profile smart
```

Known `act` differences are reported before execution. Deployment-class steps are
skipped by default and the semantic difference is recorded; `--allow-deploy` is an
explicit opt-in outside this safe default.

## 10. Use MCP from an agent

Prefer the co-located remote MCP server for live remote operations. Start a detached
test with `run_tests` or `job_start`, keep the returned `job_id`, poll `job_status`, and
fetch bounded pages with `job_output`. Optional MCP progress is a compact convenience;
after disconnect, resume from the durable cursor.

Agents should normally use `smart` or a named compact profile, inspect status/health
before cancelling quiet jobs, and request `full` output only when needed.

## 11. Explicit local override

```bash
./sb test --local --timeout 900 node-unit
```

The same job/status/output concepts apply locally, and the result clearly reports the
local target. Local execution is not removed; it is deliberate when a configured remote
default exists.

## 12. Reset and cleanup

```bash
./sb workspace reset --remote scaleway-sandbox --workspace node-unit --yes
./sb workspace destroy --remote scaleway-sandbox --workspace node-unit --yes
```

Reset/destroy refuse to run while the workspace has active jobs. Job cleanup is scoped:

```bash
./sb job-cleanup JOB_ID --remote scaleway-sandbox --logs --artifacts --yes
```

Production deployment, release publishing, and destructive cleanup outside the named
job/workspace remain outside this workflow.

## 13. Durable workspace metadata/index migration

Use a disposable project identity and isolated base for this metadata-only check. The
remote controls below intentionally omit `--project-dir` and do not run reset, destroy,
cleanup, deploy, or network release.

```bash
export SANDBOX_HOME="$(mktemp -d)/sandbox"
./sb workspace migrate --remote scaleway-sandbox --project-identity PROJECT_ID --json
./sb workspace migrate --remote scaleway-sandbox --plan-id PLAN_ID --confirm --json
./sb workspace list --remote scaleway-sandbox --project-identity PROJECT_ID --json
./sb workspace status --remote scaleway-sandbox --workspace-id WORKSPACE_ID --json
```

Expected:

- the plan includes an opaque plan ID, complete legacy inventory digest, index generation,
  expiry, and one adopted/unresolved/conflict/invalid decision per source;
- the remote index is `$SANDBOX_HOME/runtime/workspaces/index.sqlite3` on that host and
  the legacy `runtime/jobs/workspaces/<legacy-namespace>/<label>/workspace.json` bytes
  are unchanged;
- list/status reports `workspace_index_incomplete` rather than silently returning empty
  when relevant legacy records remain unresolved, and status by workspace ID works when
  the checkout locator is absent;
- modified inventory or index generation fails closed with
  `workspace_migration_plan_stale` and does not partially adopt rows;
- relocation changes only index/locator paths and leaves project files, uploads, snapshots,
  database volumes, jobs, containers, and networks unchanged;
- resource status receives typed workspace ownership and does not open the index.

Repeat the plan/apply operation to verify idempotency. Keep unresolved/conflict decisions
visible for explicit operator review; never resolve them from names, age, or a path alone.
