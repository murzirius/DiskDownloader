# Initial validation

This is an early development build, not a production-readiness certification.

## Checked locally on Windows

- Automated tests cover original-byte preservation with synthetic EXIF date and GPS fields, existing-file protection, case-insensitive name collisions, unsafe names, empty folders, partial-prefix verification, valid and invalid ranges, servers ignoring Range, source changes, checksum failures and low-space pause.
- A child process is abruptly terminated after a durable partial checkpoint. Restart preserves SQLite integrity and continues from the saved offset.
- Persisted scan pagination resumes without rereading completed pages. Device checking uses the task manifest and does not enumerate unrelated logs or reports.
- HTTP tests cover read-only authorization headers, quota accounting, rate-limit responses, permission errors and safe content redirects. Bearer credentials are not forwarded to signed content hosts.
- Local interface tests cover host validation, cross-origin/CSRF protection, private error details, malicious OAuth endpoints, language selection and quota-project separation.
- Refresh concurrency tests check that eight waiting downloads refresh once and that status reads do not wait for a network token refresh.
- A real Windows EXE was launched with isolated state. Its local HTTP service, bundled font, language override, single-instance behavior, native status window, native folder picker cancellation and complete shutdown were checked.
- Headless Chrome checked English/Russian switching, font loading and overflow at desktop, tablet and mobile widths, including populated synthetic task/file lists.

The Windows symlink test is skipped when the operating-system account lacks symlink creation privileges. Hardlinked partials are tested separately. CI also runs the tests on Linux and macOS, where symlink behavior can be checked independently.

## Synthetic scale check

A local paginated fixture supplied 55,000 metadata entries in 55 pages. Persisting the manifest took approximately 3.34 seconds on the development machine. Peak Python allocations measured by `tracemalloc` were approximately 0.60 MB during this scan; that excludes SQLite/native allocations, process baseline and operating-system caches.

This measures local processing only. It does **not** measure Google network latency, actual Drive scan time, disk hashing speed, download speed, total process RAM or NAS behavior.

## Not yet checked

- Real Google OAuth consent, account policies, project quotas and token refresh against Google's live endpoints.
- An end-to-end private Google Drive archive download, including live large-file redirects and shared-drive permissions.
- Native macOS/Linux desktop interaction on physical devices. CI headless smoke checks do not establish desktop usability or system trust/notarization.
- Long-running NAS disconnects, power-loss behavior of physical disks and antivirus-specific interactions.
- Whether personal-project use qualifies for Google's verification exceptions in every distribution scenario.

No real user tokens, personal media or private account fixtures are included in the repository or automated checks.
