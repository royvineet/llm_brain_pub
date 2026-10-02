#!/usr/bin/env python3
"""
Non-interactive helper for document filing. Used by the /docs Claude Code skill.

Subcommands:
  list                     List pending files in the Google Drive scanned folder (JSON)
  file <file-id>           Download, upload to NAS, index, and delete from Drive
    --path PATH            NAS filing path, e.g. finance/tax/me
    --doc-type TYPE        Snake_case doc type, e.g. itr2_return
    --doc-date DATE        YYYY-MM-DD, YYYY-MM, or YYYY
    --filename FILENAME    Final filename with extension
    --tags TAGS            Comma-separated tags
    --description DESC     One-line description
"""

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"

SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
    "text/plain",
}


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def resolve(p):
    return Path(p).expanduser()


# ── Google Drive ──────────────────────────────────────────────────────────────

def get_drive_credentials(cfg):
    from google_auth import DRIVE_SCOPES, get_credentials

    return get_credentials(
        resolve(cfg["gdrive"]["credentials"]),
        resolve(cfg["gdrive"]["drive_token"]),
        DRIVE_SCOPES,
    )


def get_drive_service(cfg):
    from googleapiclient.discovery import build
    creds = get_drive_credentials(cfg)
    return build("drive", "v3", credentials=creds)


def find_scanned_folder(service, folder_name):
    q = (
        f"name='{folder_name}'"
        " and mimeType='application/vnd.google-apps.folder'"
        " and 'root' in parents and trashed=false"
    )
    results = service.files().list(q=q, fields="files(id, name)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def download_file(service, file_id, dest_path):
    from googleapiclient.http import MediaIoBaseDownload
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        dl = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = dl.next_chunk()


def delete_drive_file(service, file_id):
    service.files().delete(fileId=file_id).execute()


# ── NAS / SMB ─────────────────────────────────────────────────────────────────

def build_unc(nas_cfg, *parts):
    inner = "\\".join(
        [nas_cfg["base_path"].replace("/", "\\")] + [str(p) for p in parts]
    )
    return f"\\\\{nas_cfg['host']}\\{nas_cfg['share']}\\{inner}"


def setup_smb(nas_cfg):
    import smbclient
    smbclient.register_session(
        nas_cfg["host"],
        username=nas_cfg["username"],
        password=nas_cfg["password"],
    )
    return smbclient


def upload_to_nas(smbclient, nas_cfg, local_path, doc_path, filename):
    parts = [p for p in doc_path.split("/") if p]
    dir_unc = build_unc(nas_cfg, *parts)
    file_unc = build_unc(nas_cfg, *parts, filename)
    smbclient.makedirs(dir_unc, exist_ok=True)
    with open(local_path, "rb") as src:
        data = src.read()
    with smbclient.open_file(file_unc, mode="wb") as dst:
        dst.write(data)
    return file_unc


def unc_to_smb_url(nas_cfg, file_unc):
    inner = file_unc.replace(f"\\\\{nas_cfg['host']}\\{nas_cfg['share']}", "")
    return f"smb://{nas_cfg['host']}/{nas_cfg['share']}/{inner.lstrip(chr(92)).replace(chr(92), '/')}"


# ── Taxonomy ──────────────────────────────────────────────────────────────────

def load_taxonomy(path):
    if not path.exists():
        return {"tree": {}, "tags": []}
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("tree", {})
    data.setdefault("tags", [])
    return data


def save_taxonomy(path, data):
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=True)


def add_path_to_tree(tree, path_str):
    node = tree
    for part in [p.strip() for p in path_str.split("/") if p.strip()]:
        node = node.setdefault(part, {})


def merge_tags(known, new_tags):
    known_set = set(known)
    for t in new_tags:
        if t not in known_set:
            known.append(t)
            known_set.add(t)


# ── documents.yaml ────────────────────────────────────────────────────────────

def load_documents(path):
    if not path.exists():
        return {"documents": []}
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("documents", [])
    return data


def save_documents(path, data):
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


# ── Subcommands ───────────────────────────────────────────────────────────────

def cmd_list(cfg):
    """Print pending scanned files as JSON."""
    service = get_drive_service(cfg)
    folder_id = find_scanned_folder(service, cfg["gdrive"]["scanned_folder"])
    if not folder_id:
        print(json.dumps([]))
        return

    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name, mimeType)",
        orderBy="createdTime",
    ).execute()

    files = [
        f for f in results.get("files", [])
        if f.get("mimeType") in SUPPORTED_MIME_TYPES
    ]
    print(json.dumps(files, indent=2))


def cmd_file(cfg, args):
    """Download, upload to NAS, index, and delete from Drive."""
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    service = get_drive_service(cfg)
    nas_cfg = cfg["nas"]
    docs_path = resolve(cfg["storage"]["documents"])
    taxonomy_path = resolve(cfg["storage"]["doc_taxonomy"])

    ext = Path(args.filename).suffix.lower() or ".pdf"

    # Download
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        try:
            download_file(service, args.file_id, tmp_path)
        except Exception as e:
            print(f"ERROR: download failed: {e}", file=sys.stderr)
            sys.exit(1)

        # Upload to NAS
        try:
            smb = setup_smb(nas_cfg)
            file_unc = upload_to_nas(smb, nas_cfg, tmp_path, args.path, args.filename)
        except Exception as e:
            print(f"ERROR: NAS upload failed: {e}", file=sys.stderr)
            sys.exit(1)
    finally:
        tmp_path.unlink(missing_ok=True)

    # Index
    smb_url = unc_to_smb_url(nas_cfg, file_unc)
    docs_data = load_documents(docs_path)
    doc_id = max((d["id"] for d in docs_data["documents"]), default=0) + 1
    docs_data["documents"].append({
        "id": doc_id,
        "filename": args.filename,
        "nas_path": smb_url,
        "path": args.path,
        "doc_type": args.doc_type,
        "doc_date": args.doc_date,
        "tags": tags,
        "description": args.description or "",
        "original_filename": args.original_name or args.filename,
        "filed_at": date.today().isoformat(),
    })
    save_documents(docs_path, docs_data)

    # Update taxonomy
    taxonomy = load_taxonomy(taxonomy_path)
    add_path_to_tree(taxonomy["tree"], args.path)
    merge_tags(taxonomy["tags"], tags)
    save_taxonomy(taxonomy_path, taxonomy)

    # Delete from Drive
    try:
        delete_drive_file(service, args.file_id)
    except Exception as e:
        print(f"WARNING: filed but could not delete from Drive: {e}", file=sys.stderr)

    print(f"OK: filed as document #{doc_id} → {args.path}/{args.filename}")
    print(f"    NAS: {smb_url}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Document filing helper for /docs skill")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="List pending files in Drive scanned folder (JSON)")

    fp = sub.add_parser("file", help="File a document to NAS and index it")
    fp.add_argument("file_id", help="Google Drive file ID")
    fp.add_argument("--path", required=True, help="NAS filing path, e.g. finance/tax/me")
    fp.add_argument("--doc-type", required=True, dest="doc_type")
    fp.add_argument("--doc-date", required=True, dest="doc_date")
    fp.add_argument("--filename", required=True)
    fp.add_argument("--tags", default="", help="Comma-separated tags")
    fp.add_argument("--description", default="")
    fp.add_argument("--original-name", dest="original_name", default="")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    cfg = load_config()

    if args.cmd == "list":
        cmd_list(cfg)
    elif args.cmd == "file":
        cmd_file(cfg, args)


if __name__ == "__main__":
    main()
