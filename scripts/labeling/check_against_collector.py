#!/usr/bin/env python3
"""
Compare court_pdf_labels.csv with what the collector itself recorded.

Prints counts only: no case names, no document text.

  1. PDFs per folder vs the frozen snapshot in data/collected/README.md
     (23 Sep 2026: 820 free RECAP PDFs on 738 dockets)
  2. Manifest coverage: how many records in each collector manifest were
     matched to a PDF on disk
  3. Document type vs the collector's own `document_kind` tags in
     public/fraud_court_lookup.jsonl (264 fraud filings)
  4. Duplicates: identical files on different docket entries, and whether
     the collector recorded them as the same case
  5. URL check: every URL is a CourtListener link

Usage:
  python check_against_collector.py --root <recap folder> --labels court_pdf_labels.csv
  (add --fraud-lookup <path to fraud_court_lookup.jsonl> if it isn't found
   automatically; it is in the repo at data/collected/public/)
"""
import argparse, csv, json, re
from collections import Counter, defaultdict
from pathlib import Path

FROZEN = {"fraud": 277, "bulk": 506, "trafficking": 13, "cyber": 12, "csea": 12}
FROZEN_FRAUD_SUB = {"from_press": 164}

KIND_MAP = {  # collector document_kind -> our doc_type
    "indictment": "indictment", "sealed indictment": "indictment",
    "superseding indictment": "superseding_indictment",
    "plea": "plea", "sentencing": "sentencing_memorandum",
    "complaint": "complaint", "judgment": "judgment",
    "transcript": "transcript", "docket": "docket",
    "factual basis": "other", "affidavit": "other", "other": "other",
}


def load(p):
    p = Path(p)
    if p.suffix == ".jsonl":
        return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d, list):
        return d
    return next((v for v in d.values() if isinstance(v, list)), [])


def segs(path):
    parts = path.split("/")
    return parts[1:] if parts and parts[0].lower() == "recap" else parts


def table(rows, head):
    w = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
    fmt = "  ".join(f"{{:<{n}}}" for n in w)
    print("  " + fmt.format(*head))
    for r in rows:
        print("  " + fmt.format(*r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--labels", type=Path, default=Path("court_pdf_labels.csv"))
    ap.add_argument("--fraud-lookup", type=Path)
    a = ap.parse_args()
    root = a.root.resolve()
    rows = list(csv.DictReader(a.labels.open(newline="", encoding="utf-8")))
    has_dups = "duplicate_of" in (rows[0] if rows else {})

    # 1 ---------------------------------------------------------------
    print("\n1. PDFs per folder vs the collector's frozen snapshot")
    disk = Counter(segs(r["path"])[0] for r in rows)
    sub = Counter(segs(r["path"])[1] for r in rows
                  if segs(r["path"])[0] == "fraud" and len(segs(r["path"])) > 2)
    out = []
    for k in sorted(set(FROZEN) | set(disk)):
        out.append((k, FROZEN.get(k, "-"), disk.get(k, 0),
                    disk.get(k, 0) - FROZEN.get(k, 0)))
    out.append(("TOTAL", sum(FROZEN.values()), sum(disk.values()),
                sum(disk.values()) - sum(FROZEN.values())))
    table(out, ("folder", "collector", "on disk", "difference"))
    for k, n in FROZEN_FRAUD_SUB.items():
        print(f"  fraud/{k}/: collector {n}, on disk {sub.get(k, 0)}")

    # 2 ---------------------------------------------------------------
    print("\n2. Manifest coverage (records matched to a PDF on disk)")
    used = Counter()
    for r in rows:
        for m in re.findall(r"[A-Za-z_]+\.jsonl?|court_manifest\.json", r["label_origin"]):
            used[(segs(r["path"])[0], m)] += 1
    out = []
    for m in sorted(root.rglob("*")):
        if m.name not in ("court_manifest.json", "recap_links.jsonl",
                          "fraud_study.jsonl", "from_press.jsonl"):
            continue
        n = len(load(m))
        top = segs(m.relative_to(root).as_posix())[0]
        out.append((m.relative_to(root).as_posix(), n, used.get((top, m.name), 0)))
    table(out, ("manifest", "records", "PDFs labeled from it"))
    print("  (a PDF matched by two manifests counts under the first one used)")

    # 3 ---------------------------------------------------------------
    print("\n3. Document type vs the collector's own document_kind (fraud)")
    fl = a.fraud_lookup
    if not fl:
        for c in [root.parent / "public" / "fraud_court_lookup.jsonl",
                  root / "public" / "fraud_court_lookup.jsonl"]:
            if c.is_file():
                fl = c
                break
    if not fl or not Path(fl).is_file():
        print("  fraud_court_lookup.jsonl not found; pass --fraud-lookup <path>")
    else:
        by_id, by_url = {}, {}
        for rec in load(fl):
            kind = (rec.get("document_kind") or rec.get("kind") or "").strip().lower()
            if rec.get("document_id"):
                by_id[str(rec["document_id"])] = kind
            if rec.get("absolute_url"):
                by_url[rec["absolute_url"].rstrip("/")] = kind
        pairs = Counter()
        joined = 0
        for r in rows:
            if segs(r["path"])[0] != "fraud":
                continue
            stem = Path(r["path"]).stem
            kind = by_id.get(stem) or by_url.get(r["url"].rstrip("/"))
            if kind is None:
                continue
            joined += 1
            pairs[(KIND_MAP.get(kind, "other"), r["doc_type"] or "(blank)", kind)] += 1
        agree = sum(n for (exp, got, _), n in pairs.items() if exp == got)
        print(f"  joined {joined} of {len(by_id)} collector records to a labeled PDF")
        if joined:
            print(f"  agree: {agree} ({agree / joined:.0%})")
            out = [(k, exp, got, n) for (exp, got, k), n in
                   sorted(pairs.items(), key=lambda x: -x[1]) if exp != got]
            if out:
                print("  disagreements:")
                table(out, ("collector kind", "expected", "our label", "files"))

    # 4 ---------------------------------------------------------------
    print("\n4. Duplicates")
    if not has_dups:
        print("  labels file has no duplicate_of column; rerun the labeler")
    else:
        case_of = {}
        for m in root.rglob("recap_links.jsonl"):
            for rec in load(m):
                if rec.get("pdf"):
                    case_of[str(rec["pdf"]).replace("\\", "/").rsplit("/", 1)[-1]] = \
                        re.sub(r"\W+", " ", (rec.get("case_name") or "").lower()).strip()
        groups = defaultdict(list)
        for r in rows:
            key = r["duplicate_of"] or r["path"]
            groups[key].append(r)
        groups = {k: g for k, g in groups.items() if len(g) > 1}
        same_folder = sum(1 for g in groups.values()
                          if len({segs(r["path"])[0] for r in g}) == 1)
        same_case = diff_case = unknown = 0
        for g in groups.values():
            names = {case_of.get(Path(r["path"]).name) for r in g}
            if None in names or "" in names:
                unknown += 1
            elif len(names) == 1:
                same_case += 1
            else:
                diff_case += 1
        print(f"  groups of identical files: {len(groups)}  "
              f"(extra copies: {sum(len(g) - 1 for g in groups.values())})")
        print(f"  all copies in one folder: {same_folder}; spread across folders: "
              f"{len(groups) - same_folder}")
        print(f"  collector case name the same for every copy: {same_case}; "
              f"different: {diff_case}; not in recap_links: {unknown}")
        print("  (same file on several docket entries is usually one indictment "
              "filed on each co-defendant's docket)")
    junk = sum(1 for r in rows if "not_a_pdf" in r["needs_review"])
    print(f"  files that are not real PDFs: {junk}")

    # 5 ---------------------------------------------------------------
    print("\n5. URLs")
    kinds = Counter()
    for r in rows:
        u = r["url"]
        if not u:
            kinds["missing"] += 1
        elif u.startswith("https://storage.courtlistener.com/"):
            kinds["CourtListener PDF link"] += 1
        elif u.startswith("https://www.courtlistener.com/"):
            kinds["CourtListener page link"] += 1
        else:
            kinds["NOT a CourtListener link"] += 1
    table(sorted(kinds.items(), key=lambda x: -x[1]), ("url kind", "files"))


if __name__ == "__main__":
    main()
