# Offline backup and recovery

This is the v1 single-instance, stopped-service recovery contract. It supports
POSIX ownership and private file permissions; Linux is the deployment target.
Windows recovery fails closed until an ACL-aware implementation is available.
This restriction does not change the local-development service's platform scope.

## What is protected

- A snapshot uses SQLite's backup API, including committed WAL transactions. It
  never copies the active database's main file as a substitute for a snapshot
- The service/worker lock and a SQLite write reservation remain held while the
  database and canonical job sources/Markdown/previews are captured together
- Private directories are `0700`, files are `0600`, and inputs must belong to the
  operator. Symlinks, hard-linked files, special files, parent traversal, extra
  payloads, malformed metadata, missing files and SHA-256 mismatches fail closed
- A versioned manifest binds each relative payload path to its size and SHA-256.
  File counts, ledger rows, metadata size and aggregate bytes are bounded
- Restore creates a new directory, or uses an existing private empty directory.
  It never replaces a working data directory or merges into existing contents
- All restored sessions are removed and every unused invite is revoked
- A separate, current journal filters deletions, current account state and
  privileges, password-reset events, and already-consumed daily quota

The backup database contains password hashes, historical session/invite hashes,
account information and document metadata. Sources and results contain uploaded
content. These are private operational artifacts, not support attachments,
release assets or Git files. Do not send them in chat, put them in web-served
storage or attach them to an issue. The journal deliberately contains generated
IDs, timestamps, account flags/limits and counters, never passwords or hashes.
It is still private account metadata.

SHA-256 detects damaged or accidentally changed artifacts; it does not prove
origin or protect against a malicious operator who can rewrite artifacts and
checksums. Use trusted, access-controlled backup storage and separate appropriate
storage encryption. This module does not implement encryption or signatures.

## The independent journal is mandatory

A T0 snapshot cannot know that a user deleted a job, an administrator disabled an
account, or an operator reset a password at T1. Restoring only T0 at T2 would undo
those protections. A snapshot's own ledger is therefore never accepted as proof
of the current state.

Export the recovery journal from the **current source database after the final
service shutdown**, independently of the older snapshot. Keep the source stopped
through the recovery/cutover decision. The command rejects exporting a journal
from a snapshot directory. Each source has a durable recovery UUID; journals from
other sources and journals older than the snapshot are rejected. Existing known
tombstones, revocations and carried password resets cannot disappear from a newer
journal.

The timestamp check is necessary, but does not prove that an export includes the
last mutation. `--confirm-latest-journal` is the operator's explicit assertion
that the journal captures every mutation through the final source shutdown.
Do not pass it simply because a journal file exists or its timestamp is newer
than the backup. If the source restarted or changed after export, stop it and
export a **new** journal before proceeding.

If the current source is lost and no independently retained journal is known to
cover its final state, there is a recovery gap. **Do not restore the old snapshot
for service use.** This tool has no missing-journal or incomplete-journal bypass.
An old backup cannot reconstruct unknown deletions, password changes, disables
or quota consumption. The operator must recover the current ledger or establish
a separately reviewed recovery plan. Never fabricate journal entries, timestamps
or an assertion of currentness to make the command pass.

Journals are exports at explicit stopped-service checkpoints, not automatic
continuous replication. Protecting against total source loss requires an
operator-managed journal capture/retention practice with a known recovery point.
This release does not promise zero-loss disaster recovery after later mutations.

## Operator procedure

Use an authorized service account and deployment-specific stop procedure. The
commands below use placeholder paths; they do not stop/start services, change
system configuration, create real accounts or authorize a production rollout.
Use the same approved application/runtime version that produced the snapshot.

1. Stop incoming work, stop the service, and wait for parser workers to exit.
   Also stop interactive account/admin changes. Do not remove `worker.lock` to
   bypass an active worker. Stop/check the owning process instead
2. Prepare a private, operator-owned parent outside the live data tree. The
   snapshot destination and journal file must be new names. Keep the journal
   outside the snapshot directory so the snapshot stays immutable
3. Create a snapshot while stopped:

   ```bash
   markitdown-web backup \
     --data-dir /private/live \
     --backup-dir /private/recovery/snapshot-T0
   ```

   Capture and retain a journal at this stopped checkpoint as appropriate for
   your approved backup policy. If the service subsequently resumes, that journal
   is no longer known to cover later mutations
4. Immediately before an actual restore, after the final source shutdown, export
   a new journal from the current live source:

   ```bash
   markitdown-web export-recovery-journal \
     --data-dir /private/live \
     --journal-file /private/recovery/current-journal-T2.json
   ```

5. Confirm independently that this export covers the final source state and
   restore into a separate new directory:

   ```bash
   markitdown-web restore \
     --backup-dir /private/recovery/snapshot-T0 \
     --journal-file /private/recovery/current-journal-T2.json \
     --target-dir /private/restored-T2 \
     --confirm-latest-journal
   ```

6. Inspect the count-only result, verify the restored directory remains private,
   and rehearse expected owner/user flows in the approved isolated environment.
   Select/switch the service data directory only under the deployment/cutover
   procedure. These commands do not switch it automatically. Keep the old source
   untouched until verification and the retention decision are complete

No password, invitation, session token, document content or filename is printed
by these commands. Error messages omit raw SQLite/private path details. Do not
add debug dumps of the SQLite database or journal to shared logs.

## State after restore

- Jobs in the current deletion ledger are removed, including their dependent
  attempts/retry records. The durable tombstone ledger survives restore and is
  not subject to ordinary job-history expiry
- Latest account activation, role and account limits apply. A snapshot account
  missing from the current account list is disabled. Newly created accounts
  absent from the snapshot are not invented from journal metadata
- Disabled accounts retain expired job metadata, but their payloads are not
  restored. A durable `restore_filtered` tombstone prevents those payloads from
  returning during another older restore, even after later account reactivation
- Naturally expired jobs keep expired metadata without source/result bytes
- Old queued/running jobs become failed and require owner-initiated retry.
  Attempts record `backup_restored`; interrupted running attempts are finalized
  with no worker process assumed to survive the stopped-service boundary
- Current daily usage is merged using the maximum of snapshot/current counts.
  Restore, deletion, failures and interruption do not refund already-used quota
- All sessions are invalidated. Unused invitations remain unusable, including
  invitations created before the snapshot. Create fresh invitations as needed
- If a password was reset after the snapshot, the restored old hash is replaced
  with an unusable marker. Both the pre-reset and post-reset passwords fail
  safely until the operator runs the interactive `reset-password` command on
  the restored data directory. No password hash is placed in the journal
- Password invalidation preserves the account's latest active/disabled state.
  Resetting a disabled account's password does not reactivate it. An active sole
  owner can regain access via an interactive fresh reset without an automatic
  account reactivation or bootstrap bypass

Example of an authorized operator's subsequent interactive recovery:

```bash
markitdown-web reset-password --data-dir /private/restored-T2
```

The command requests credentials in the terminal without echo. Never pass
passwords in argv, environment variables, files, logs or chat. After a restore,
all users must sign in again, even where their existing password remains valid.

## Bounds and failure handling

The current format supports a database up to 256 MiB, metadata documents up to
16 MiB each, up to 100,000 manifest files and up to 100,000 rows per journal
category, and at most 4 GiB total snapshot payload. Individual document sources
remain at most 20 MiB, Markdown 2 MiB, and sanitized preview 4 MiB. These are
recovery implementation limits, not capacity or throughput claims.

Conversion scratch, orphan scratch, raw worker `result.json`, runtime/model
files, configuration secrets, environment variables and the live SQLite WAL/SHM
files are not copied into the snapshot. Committed WAL content is included via
the backup API. Restore does not reproduce a parser installation, model cache,
TLS configuration or other host prerequisites.

A lock conflict, active writer, invalid ledger, missing/current-source failure,
insecure permission, unsupported platform, changed source digest, corrupt
snapshot or nonempty target aborts the operation. Do not weaken checks to force
recovery. Correct the cause and retry using new output names. Existing source
and completed backups are not replaced. Partial newly created backup/restore
trees are cleaned after a handled failure; interrupted power/process failure
may leave an incomplete private directory, which must not be treated as ready.
A manifest/checksum validates captured bytes, not target-host deployment safety.

Backup retention is separate from live document retention. Deleting a live job
does not physically erase an older backup; journal filtering prevents restoring
its accessible job/files through this tool. This is not secure erasure, and
cannot delete copies already downloaded or externally copied by users. Set and
review an explicit backup/journal retention policy outside this code release.
Never discard the last complete current journal while keeping older snapshots
that require it.

## Synthetic acceptance

`packages/markitdown-web/tests/test_recovery.py` uses only generated fake accounts
and temporary synthetic files. It covers roundtrip/private permissions under a
normal umask; committed WAL data; T0 backup/T1 deletion and disable/T2 restore;
quota preservation; repeated restore; account-role/limit rollback prevention;
post-snapshot reset and fresh interactive-service recovery semantics; queued and
running attempts; expiry; absent/stale/wrong-source/malformed journals;
worker/database locks; corruption/missing/injected payloads; traversal,
symlinks/hardlinks; strict metadata bounds; nonempty destination preservation;
and unsupported permission platforms. These are local engineering tests, not
production backup operations or target-host disaster-recovery evidence.
