"""Build a native application on the target operating system."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).parent
data = root / "diskdownloader"
args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "DiskDownloader",
        "--onedir", "--windowed", "--collect-all", "keyring", "--collect-all", "google_auth_oauthlib",
        "--collect-all", "google.auth", "--collect-all", "certifi",
        "--add-data", str(data / "templates") + os.pathsep + "diskdownloader/templates",
        "--add-data", str(data / "static") + os.pathsep + "diskdownloader/static",
        "--add-data", str(data / "locales") + os.pathsep + "diskdownloader/locales"]
for name in ("LICENSE", "PRIVACY.md", "README.md"):
    args += ["--add-data", str(root / name) + os.pathsep + "licenses"]
if sys.platform == "win32":
    args += ["--manifest", str(root / "windows.manifest")]
args += [str(root / "run.py")]
subprocess.run(args, cwd=root, check=True)

# Distribute the runtime dependencies' own notices; never include credentials or local state.
import importlib.metadata
import shutil
from packaging.requirements import Requirement
destination = root / "dist/DiskDownloader"
if not destination.is_dir():
    raise RuntimeError("Build output missing")
licenses = destination / "licenses"
licenses.mkdir(exist_ok=True)
pending = [Requirement(line).name for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
seen = set()
inventory = []
while pending:
    name = pending.pop()
    normalized = name.lower().replace("_", "-")
    if normalized in seen:
        continue
    seen.add(normalized)
    dist = importlib.metadata.distribution(name)
    inventory.append(dist.metadata["Name"] + " " + dist.version)
    for requirement in dist.requires or []:
        req = Requirement(requirement)
        if not req.marker or req.marker.evaluate({"extra": ""}):
            pending.append(req.name)
    for item in dist.files or []:
        filename = Path(str(item)).name
        if filename.lower().startswith(("license", "copying", "notice")):
            source = Path(dist.locate_file(item))
            if source.is_file():
                target = licenses / normalized / filename
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(source, target)
for name in ("LICENSE", "PRIVACY.md", "README.md"):
    shutil.copyfile(root / name, destination / name)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    shutil.copyfile(python_license, licenses / "Python-LICENSE.txt")
(destination / "THIRD_PARTY_NOTICES.txt").write_text("Runtime dependencies\n\n" + "\n".join(sorted(inventory)) + "\n\nGoogle Sans: SIL Open Font License 1.1. See bundled diskdownloader/static/fonts/OFL.txt.\n", encoding="utf-8")
if sys.platform == "darwin":
    resources = root / "dist/DiskDownloader.app/Contents/Resources"
    if resources.is_dir():
        shutil.copytree(licenses, resources / "licenses", dirs_exist_ok=True)
        for name in ("README.md", "PRIVACY.md", "LICENSE", "THIRD_PARTY_NOTICES.txt"):
            shutil.copyfile(destination / name, resources / name)
