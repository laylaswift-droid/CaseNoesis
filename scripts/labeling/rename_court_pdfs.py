#!/usr/bin/env python3
"""
Copy fully labeled court PDFs into a new folder under label-based names,
e.g. fraud_superseding_indictment_recap_0001.pdf

  * Reads court_pdf_labels.csv (made by label_court_pdfs.py).
  * Only rows with an empty needs_review column are copied and renamed.
  * Originals are never moved, renamed or changed.
  * Numbering restarts for each crime + document type + source group, in
    order of original path, so reruns give the same names.

Writes, inside --out:
  <renamed PDFs>
  release_manifest.csv  new_name, crime_type, doc_type, source, url, original_path
  unlabeled.csv         rows that still need review (not copied), with reasons
  duplicates.csv        exact copies of a kept file (not copied), and which file they copy

Usage:
  python rename_court_pdfs.py --root <recap folder> --labels court_pdf_labels.csv --out <new folder>
  add --dry-run to see the counts without copying anything
"""
import argparse, csv, os, re, shutil, sys
from collections import Counter, defaultdict
from pathlib import Path

SAFE_LABEL = re.compile(r"^[a-zA-Z0-9_]+$") #letters, digits and _only
DOC_TYPES = {"indictment", "superseding_indictment", "plea",
             "sentencing_memorandum", "complaint", "judgment",
             "transcript", "docket"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path,
                    help="the same --root you gave label_court_pdfs.py")
    ap.add_argument("--labels", type=Path, default=Path("court_pdf_labels.csv"))
    ap.add_argument("--out", required=True, type=Path,
                    help="new, empty folder for the renamed copies")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    root, out = a.root.resolve(), a.out.resolve()
    if not root.is_dir():
        sys.exit(f"--root folder not found: {root}")
    if not a.labels.is_file():
        sys.exit(f"labels file not found: {a.labels}")
    if out == root or root in out.parents:
        sys.exit("--out must be outside the --root folder, or the copies "
                 "would be picked up the next time you run the labeler.")
    if out.exists() and any(out.iterdir()) and not a.dry_run:
        sys.exit(f"--out folder is not empty: {out}\n"
                 "Pick a new folder name so nothing gets overwritten.")

    with a.labels.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    ready, unlabeled, dups = [], [], []
    for r in rows:
        if (r.get("duplicate_of") or "").strip():
            dups.append(r)
        elif (r.get("needs_review") or "").strip():
            unlabeled.append(r)
        elif not (r["crime_type"] and r["doc_type"] and r["source"]):
            r["needs_review"] = "incomplete_labels"
            unlabeled.append(r)
        elif r["doc_type"] not in DOC_TYPES:
            r["needs_review"] = "not_one_of_8_types"
            unlabeled.append(r)
        elif not (SAFE_LABEL.match(r["crime_type"]) and SAFE_LABEL.match(r["source"])):
            r["needs_review"] = "unsafe_label"
            unlabeled.append(r)
        else:
            ready.append(r)
    ready.sort(key=lambda r: r["path"])

    counters = defaultdict(int)
    plan, missing = [], []
    for r in ready:
        rel = os.path.normpath(r["path"])  # checks the path as written; links are allowed
        if (os.path.isabs(rel) or Path(rel).drive or rel == os.pardir
                or rel.startswith(os.pardir + os.sep)):
            r["needs_review"] = "path_outside_root"
            unlabeled.append(r)
            continue
        src = root / rel
        if not src.is_file():
            missing.append(r["path"])
            continue
        group = f'{r["crime_type"]}_{r["doc_type"]}_{r["source"]}'
        counters[group] += 1
        plan.append((src, f"{group}_{counters[group]:04d}.pdf", r))

    print(f"labels rows       {len(rows)}")
    print(f"to copy+rename    {len(plan)}")
    print(f"left unlabeled    {len(unlabeled)}")
    print(f"duplicate copies  {len(dups)} (skipped)")
    if missing:
        print(f"listed but not found on disk: {len(missing)} (first: {missing[0]})")
    print("\nfiles per group:")
    for g, n in sorted(counters.items()):
        print(f"  {g:<50}{n}")

    if a.dry_run:
        print("\n(dry run: nothing copied)")
        return

    out.mkdir(parents=True, exist_ok=True)
    with (out / "release_manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["new_name", "crime_type", "doc_type", "source", "url",
                    "original_path", "sha256"])
        for src, new, r in plan:
            shutil.copy2(src, out / new)
            w.writerow([new, r["crime_type"], r["doc_type"], r["source"],
                        r["url"], r["path"], r.get("sha256", "")])
    with (out / "unlabeled.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["original_path", "crime_type", "doc_type", "url",
                    "needs_review"])
        for r in unlabeled:
            w.writerow([r["path"], r["crime_type"], r["doc_type"], r["url"],
                        r["needs_review"]])
    with (out / "duplicates.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["original_path", "duplicate_of", "sha256"])
        for r in dups:
            w.writerow([r["path"], r["duplicate_of"], r.get("sha256", "")])
    print(f"\ncopied {len(plan)} files to {out}")
    print("wrote release_manifest.csv, unlabeled.csv and duplicates.csv there too")


if __name__ == "__main__":
    main()
