# DiskDownloader

Download your Google Drive archive to your own computer. Original files, embedded photo dates and GPS data stay intact. Downloads go directly from Google to your device.

**Early development version.** Test a small folder before exporting an important archive. Keep your cloud copy until you have checked the downloaded files and the report.

## What it does

- Downloads a folder and its subfolders, or individual files.
- Downloads original file content without resizing, previews or rewriting metadata.
- Checks available cloud checksums and file sizes; computes a local SHA-256.
- Checks files already present before downloading missing content.
- Preserves partial files and resumes supported downloads after interruption.
- Preserves scan pagination and discovered files when a task is paused or the app restarts.
- Keeps existing different files; resolves name collisions without overwriting them.
- Pauses when the destination cannot be written or safe free space falls below 500 MB.
- Shows local quota estimates and links to the project's authoritative Google Cloud quota page.
- Selects English or Russian from the browser's language preferences, with an explicit language override. Other languages fall back to English. The native status window initially uses the operating system's language.
- Uses a bundled Google Sans font, including Cyrillic, without contacting a font CDN.

Google Docs, Sheets, Slides, Google Vids and shortcuts are reported as unsupported. This version downloads stored originals; it does not export Google-native documents or follow shortcuts. Google Photos is a different service and is not supported.

## Run from source

Requires Python 3.11 or newer. Windows and macOS normally include the required Tk support in their Python installers. Linux desktop users may need their distribution's `python3-tk` package. A system credential-store backend is recommended for remembering the connection.

```sh
python -m venv .venv
```

Activate the environment, then:

```sh
python -m pip install -r requirements.txt
python run.py
```

The small status window opens the local browser interface. Closing the browser tab does not stop downloads. Use **Exit application** to save progress and stop the service.

For a local Linux service that survives closing the terminal:

```sh
python run.py --background --no-browser
```

Run `python run.py` again to open the existing instance. Headless operation has no native folder picker; enter an absolute destination path. The service listens on `127.0.0.1`, selects a free local port and records the address in its private state directory. It is not a remotely accessible web service.

## Connect your own Google project

1. Open [Google Cloud Console](https://console.cloud.google.com/projectcreate) and create a project.
2. Under **APIs & Services → Library**, enable **Google Drive API**.
3. Configure **Google Auth Platform**. For a personal test, choose an external audience and add your own account to **Test users**. Request only `https://www.googleapis.com/auth/drive.readonly`.
4. Create an OAuth client of type **Desktop app**, then download its JSON configuration. A service-account key or plain API key will not work.
5. In DiskDownloader, choose **Choose client JSON**, select the downloaded file and click **Connect Google Drive**.
6. Complete sign-in in the system browser using the account that can access the archive.

In Google's **Testing** status, Drive authorizations expire after seven days. Personal-use exceptions, publishing status and verification requirements depend on how the project is used. Changing to **In production** does not itself verify an app or guarantee permanent access. Read [Google's personal-use and verification rules](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification#exceptions-verification).

## Download an archive

1. Open the cloud folder you want to save.
2. Choose a destination on your device. This folder receives the selected cloud folder's **contents**.
3. Click **Download this folder**. Start with four simultaneous files.
4. Check the result and download the CSV report. Unsupported files and failures are listed there.

To resume, connect the same project and Google account, then click **Continue / check missing files**. Existing task history is checked before new downloads start. To discover files newly added to Google Drive after the original scan completed, start a new export task to the same destination.

Embedded metadata can only be preserved if it exists in the content Google provides. A date or place displayed by a photo service is not necessarily embedded EXIF. DiskDownloader does not invent missing dates or coordinates. Filesystem modification times are restored where the destination supports them; filesystem creation times are not promised.

## Quota panel

The panel is **local accounting**, not an authoritative remaining-key balance, billing dashboard or automatic safety cap. It counts only this installation's Drive request attempts and received file data, grouped by Cloud project. It cannot see another application or device using the same project.

Request units are estimates based on Google's published method costs (`files.list`: 100; `files.get`/`about.get`: 5). Media transfers use `files.get?alt=media`; any difference in Google's actual accounting is not discoverable from the response. Retries are included. Traffic counts payload received while transferring originals, including retried data; metadata, transport overhead and other applications are excluded. Traffic counters are periodically saved and may omit the most recent interval after abrupt termination.

Minute counters use the current UTC calendar minute, not Google's exact enforcement window. The daily display uses minute buckets from the last 24 hours. Neither window exactly reproduces Google's enforcement or billing boundaries. Default references are documentation values dated 29 October 2026, not retrieved project limits. Edit the display references to match your [Google Cloud quota page](https://console.cloud.google.com/apis/api/drive.googleapis.com/quotas).

The 1 TB reference is a published pricing-related threshold. It is not a guarantee of a free allowance, hard cutoff or a known price. Standard use and future paid overages are described in [Google's current limits](https://developers.google.com/workspace/drive/api/guides/limits). Reaching a displayed reference does not stop the app; actual Google limit responses cause retries and, if unresolved, pause the task. Do not rotate projects or credentials to evade restrictions.

## Privacy and storage

See [PRIVACY.md](PRIVACY.md). No account, download or quota data is sent to the project's developer. History, filenames, destination paths, checksums and quota estimates are stored locally. Credentials use the operating system's credential store when available; without one, connection data stays in memory until exit. No plaintext token file is created.

Partial downloads live in a reserved `.diskdownloader` folder inside the chosen destination. Do not edit or delete them while a task is active. Files, reports and unrelated logs are not recursively scanned to reconstruct download history: checking uses the task's own file manifest.

## Development

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
python build.py
```

Build on the target platform. Native bundles are experimental until their startup, browser interface and shutdown have been tested. Real Google sign-in and private-account behavior require a separately configured test account; synthetic tests are not a substitute for that validation.

Some filesystem safeguards were adapted from [YaDiskDownloader](https://github.com/murzirius/YaDiskDownloader), Copyright 2026 murzirius, under MIT. Google Sans is bundled under the SIL Open Font License; see [font license](diskdownloader/static/fonts/OFL.txt). DiskDownloader is independent and is not affiliated with or endorsed by Google.

## Сообщество

Программа автоматически выбирает русский или английский язык. Инструкция подключения доступна прямо в интерфейсе: **«Как подключить»**. Если возникла проблема, создайте [issue](https://github.com/murzirius/DiskDownloader/issues). Не прикладывайте токены, JSON подключения, личные фотографии или координаты.
