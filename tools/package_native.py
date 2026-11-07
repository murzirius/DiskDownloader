"""Package a native build while preserving executable permissions and symlinks."""
from pathlib import Path
import platform
import sys
import tarfile
import tomllib
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    system = {"win32": "windows", "darwin": "macos", "linux": "linux"}[sys.platform]
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}[machine]
    source = root / "dist" / ("DiskDownloader.app" if sys.platform == "darwin" else "DiskDownloader")
    if not source.is_dir():
        raise RuntimeError(f"Native build missing: {source}")
    output = root / "release-assets"
    output.mkdir(exist_ok=True)
    name = f"DiskDownloader-{version}-{system}-{arch}"
    if sys.platform == "win32":
        destination = output / (name + ".zip")
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(source.rglob("*")):
                archive.write(item, item.relative_to(source.parent))
    else:
        destination = output / (name + ".tar.gz")
        with tarfile.open(destination, "w:gz", dereference=False) as archive:
            archive.add(source, arcname=source.name)
    print(destination.name)


if __name__ == "__main__":
    main()
