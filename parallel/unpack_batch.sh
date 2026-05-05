#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 /srv/batch.zip [batch_name]" >&2
  exit 2
fi

archive_path="$1"
batch_name="${2:-}"

/opt/ugc-pipeline-venv/bin/python - "$archive_path" "$batch_name" <<'PY'
import os
import sys
import tarfile
import zipfile
from pathlib import Path

archive = Path(sys.argv[1]).resolve()
batch_name = sys.argv[2].strip()

if not archive.is_file():
    raise SystemExit(f"archive does not exist: {archive}")

suffixes = archive.suffixes
is_zip = archive.suffix.lower() == ".zip"
is_targz = suffixes[-2:] == [".tar", ".gz"] if len(suffixes) >= 2 else False
if not is_zip and not is_targz:
    raise SystemExit("archive must be .zip or .tar.gz")

def normalize_name() -> str:
    if batch_name:
        return batch_name
    name = archive.name
    if name.endswith(".tar.gz"):
        return name[:-7]
    if name.endswith(".zip"):
        return name[:-4]
    return archive.stem

target = Path("/srv") / normalize_name()
if target.exists():
    raise SystemExit(f"target already exists: {target}")

target.mkdir(parents=True, exist_ok=False)

def safe_members_zip(zf: zipfile.ZipFile):
    for info in zf.infolist():
        member = Path(info.filename)
        if member.is_absolute() or ".." in member.parts:
            raise SystemExit(f"unsafe zip member: {info.filename}")
        yield info

def safe_members_tar(tf: tarfile.TarFile):
    for info in tf.getmembers():
        member = Path(info.name)
        if member.is_absolute() or ".." in member.parts:
            raise SystemExit(f"unsafe tar member: {info.name}")
        yield info

if is_zip:
    with zipfile.ZipFile(archive) as zf:
        members = list(safe_members_zip(zf))
        zf.extractall(target, members)
else:
    with tarfile.open(archive, "r:gz") as tf:
        members = list(safe_members_tar(tf))
        tf.extractall(target, members=members)

entries = list(target.iterdir())
if len(entries) == 1 and entries[0].is_dir():
    inner = entries[0]
    for child in list(inner.iterdir()):
        child.rename(target / child.name)
    inner.rmdir()

print(target)
PY
