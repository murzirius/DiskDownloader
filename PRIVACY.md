# Privacy

DiskDownloader runs on your computer and connects directly to Google's documented APIs after you authorize access. It requests read-only Drive access. It does not modify or delete cloud files.

## Information used

- OAuth client configuration and access/refresh tokens: used to connect to your Google account. When possible, stored using the operating system's credential store through `keyring`. Otherwise retained in process memory for the session. Not written to the history database, reports or plaintext token files.
- Google account permission identifier: stored locally to prevent resuming a task with a different account. Google account information is requested to establish this identity.
- File identifiers, names, MIME types, sizes, versions, modification dates and checksums: stored in the local task manifest to browse, download, verify and resume files.
- Destination paths and task status: stored in local history. The browser also remembers the last destination in local storage.
- Request estimates, received file bytes and quota-limit response counts: stored locally by Cloud project for the quota panel.
- Original file content: streamed to the destination you choose. Embedded location and photo dates remain part of those original files. Failed or interrupted downloads may leave partial files in the destination's `.diskdownloader` directory.

DiskDownloader does not send this information to its developer, analytics services, advertising services or a font CDN. The Google APIs necessarily receive requests, credentials and network information needed to provide the service. Google applies its own privacy policy and API terms. Following a link to GitHub or Google Cloud opens that external service in your browser.

## Storage locations

State is separate from YaDiskDownloader:

- Windows: `%LOCALAPPDATA%/DiskDownloader`
- macOS: `~/Library/Application Support/DiskDownloader`
- Linux: `$XDG_STATE_HOME/DiskDownloader`, or `~/.local/state/DiskDownloader`

The app binds only to the local loopback address and protects mutation requests with an instance token and same-origin checks. This does not protect against malicious software already running as the same operating-system user.

## Disconnecting and deleting data

Disconnect removes this app's active local connection and its selected credential-store record. You can separately revoke the project's Google access in [Google account permissions](https://myaccount.google.com/permissions). Google may revoke all grants associated with that project.

After exiting the app, you may delete its private state directory to remove history and quota estimates. This does not delete your downloaded originals or any credential-store records from other previously connected projects. These can be managed through your operating system's credential manager. Never delete partial files while a task is writing them.

No diagnostics upload is automatic. When reporting a problem, do not include OAuth JSON files, tokens, personal destination paths, original media or GPS coordinates.

The use of data received from Google APIs must adhere to the Google API Services User Data Policy, including its Limited Use requirements. Information is used only for the visible local download, verification and quota-accounting features described here.
