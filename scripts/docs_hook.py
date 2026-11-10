"""Publish existing READMEs without maintaining duplicate documentation."""

from pathlib import Path

from mkdocs.structure.files import File


ROOT = Path(__file__).resolve().parent.parent
PAGES = {
    "index.md": "README.md",
    "source.md": "connectors/source-redis-adbc/README.md",
    "destination.md": "connectors/destination-redis-adbc/README.md",
}


def on_files(files, config):
    for destination, source in PAGES.items():
        content = (ROOT / source).read_text(encoding="utf-8")
        if destination == "index.md":
            # The README is at the repo root; this page lives alongside docs/.
            content = content.replace("](docs/", "](")
        files.append(File.generated(config, destination, content=content))
    return files
