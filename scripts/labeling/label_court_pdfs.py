#!/usr/bin/env python3
"""
Build a one-row-per-PDF label manifest for the CaseNoesis court records.

Label sources, in priority order:
  1. Existing manifests next to the folders:
       court_manifest.json, fraud/from_press.jsonl, fraud/fraud_study.jsonl,
       recap/bulk/manifests/recap_links.jsonl
     (any file with those names anywhere under --root is indexed)
  2. The PDF's own file name (RECAP names, descriptive names)
  3. OPTIONAL (--peek): keyword match on the first page's text, done locally.
     Off by default. Text is never printed or written out; only the label is.

Columns: path, crime_type, doc_type, source, url, label_origin, needs_review,
         sha256, duplicate_of

  sha256       file fingerprint (identical files have identical values)
  duplicate_of blank for the copy that is kept; for other exact copies, the
               path of the kept copy. Labels are pooled across copies.

  crime_type  crime folder name (e.g. recap/fraud -> fraud); for recap/bulk/ it comes
              from the crime stored in recap_links.jsonl
  doc_type    indictment | superseding_indictment | plea |
              sentencing_memorandum | complaint | judgment | transcript |
              docket | other  (other / blank => needs_review)
  source      recap
  url         CourtListener URL (from manifest, or rebuilt from a RECAP name)

Usage:
  python label_court_pdfs.py --root /path/to/recap --out labels.csv
  (--root may be the recap folder itself or the folder that contains it)
  python label_court_pdfs.py --root ... --inspect     # show manifest keys only
"""
import argparse, csv, hashlib, json, re, sys
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, unquote

MANIFEST_NAMES = {"court_manifest.json", "from_press.jsonl",
                  "fraud_study.jsonl", "recap_links.jsonl"}

# Field names we try, in order. Run --inspect first and add yours if missing.
PATH_KEYS  = ["filepath_local", "pdf", "path", "local_path", "pdf_path", "file",
              "filename", "file_name", "saved_as", "saved_path", "output", "dest"]
URL_KEYS   = ["courtlistener_url", "url", "download_url", "absolute_url",
              "recap_url", "source_url", "link", "filepath_local", "filepath_ia"]
TYPE_KEYS  = ["filing_type", "document_type", "doc_type", "document_description",
              "short_description", "description", "entry_description", "type",
              "title"]
CRIME_KEYS = ["crime", "crime_type", "category", "offense", "offence"]
# Nested groups searched (in this order) when a field isn't at the top level
NESTED = ["observed", "inferred", "chosen_document"]

DOC_TYPES = ["indictment", "superseding_indictment", "plea",
             "sentencing_memorandum", "complaint", "judgment",
             "transcript", "docket"]

# Order matters: more specific patterns first.
PATTERNS = [
    ("transcript",            r"transcript"),
    ("superseding_indictment",r"supersed\w*[\s_-]*indict"),
    ("sentencing_memorandum", r"sentenc\w*[\s_-]*(memo|memorandum|submission)"),
    ("plea",                  r"\bplea\b|plea[\s_-]*agree"),
    ("judgment",              r"\bjudge?ment\b"),
    ("indictment",            r"\bindict"),
    ("complaint",             r"\bcomplaint\b"),
    ("docket",                r"\bdocket\b(?!\s*(no\b|no\.|number|#|entry|text))"),
]
# Filings that merely *mention* another doc type ("Motion to dismiss indictment")
NOT_THE_FILING = re.compile(
    r"^\s*(motion|order|notice|response|reply|letter|memorandum (in|of law)|"
    r"objection|stipulation|consent|exhibit|affidavit|declaration)\b", re.I)

RECAP_NAME = re.compile(r"^(gov\.uscourts\.[a-z0-9]+\.\d+)\.(\d+)\.(\d+)\.pdf$", re.I)


def norm_doc_type(text, strict_prefix=True):
    if not text:
        return ""
    t = str(text).replace("_", " ").lower()
    if t.strip() in DOC_TYPES or t.strip().replace(" ", "_") in DOC_TYPES:
        return t.strip().replace(" ", "_")
    if strict_prefix and NOT_THE_FILING.search(t):
        return "other"
    texts = [t]
    if strict_prefix:
        # The filing's own name comes first ("PLEA AGREEMENT re: count One of
        # the Indictment"); attachment lists and hearing notes come later.
        head = re.split(r"\(|\battachments?\b|\bas to\b|\bre:|\bre\b|\bby\b|"
                        r"\bfiled\b|,|;", t, maxsplit=1)[0]
        texts = [head, t]
    for text in texts:
        for label, pat in PATTERNS:
            if re.search(pat, text):
                return label
    return "other"


# Crime phrases found inside recap_links.jsonl "query" values (most specific first)
CRIME_PHRASES = ["sex trafficking", "labor trafficking", "forced labor",
                 "human trafficking", "trafficking"]


def crime_from_query(q):
    """'"sex trafficking" indictment' -> sex_trafficking; names are ignored."""
    if not isinstance(q, str):
        return ""
    t = q.lower()
    for ph in CRIME_PHRASES:
        if ph in t:
            return ph.replace(" ", "_")
    return ""


def first(rec, keys):
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", []) and not isinstance(v, (dict, list)):
            return v
    for g in NESTED:
        sub = rec.get(g)
        if isinstance(sub, dict):
            for k in keys:
                v = sub.get(k)
                if v not in (None, "", []) and not isinstance(v, (dict, list)):
                    return v
    return None


def pick_url(rec):
    """Prefer an actual CourtListener link; else build one from a relative path."""
    cd = rec.get("chosen_document") if isinstance(rec.get("chosen_document"), dict) else {}
    dl = rec.get("download") if isinstance(rec.get("download"), dict) else {}
    cands = [rec.get(k) for k in ["courtlistener_url", "download_url", "pdf",
                                   "filepath_local"]]
    cands += [cd.get("download_url"), cd.get("filepath_local"), dl.get("source_url"),
              cd.get("page_url"), rec.get("absolute_url"), rec.get("url")]
    cands = [c for c in cands if isinstance(c, str) and c]
    for c in cands:
        if "courtlistener.com" in c:
            return c
    for c in cands:  # a bare RECAP filename -> its storage URL
        u = url_from_recap_name(basename_of(c))
        if u:
            return u
    for c in cands:  # CourtListener-relative paths only, never local disk paths
        if re.match(r"^/?(recap|docket|opinion|api)/", c):
            return to_courtlistener_url(c if c.startswith(("/", "recap/")) else "/" + c)
    return ""


def to_courtlistener_url(u):
    if not u:
        return ""
    u = str(u).strip()
    if u.startswith("http"):
        return u
    if u.startswith("/recap/"):
        return "https://storage.courtlistener.com" + u
    if u.startswith("/"):
        return "https://www.courtlistener.com" + u
    if u.startswith("recap/"):
        return "https://storage.courtlistener.com/" + u
    return u


def url_from_recap_name(name):
    m = RECAP_NAME.match(name)
    if not m:
        return ""
    return f"https://storage.courtlistener.com/recap/{m.group(1)}/{name}"


def docket_key(name):
    m = RECAP_NAME.match(name)
    return m.group(1).lower() if m else ""


def basename_of(v):
    s = str(v)
    if s.startswith("http"):
        s = unquote(urlparse(s).path)
    return PurePosixPath(s.replace("\\", "/")).name


def load_records(path):
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    print(f"  ! bad JSON line {n} in {path}", file=sys.stderr)
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        yield from data
    elif isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        if lists:
            for lst in lists:
                yield from lst
        else:  # {"file.pdf": {...}, ...}
            for k, v in data.items():
                if isinstance(v, dict):
                    yield {"filename": k, **v}


def find_manifests(root):
    return sorted(p for p in root.rglob("*") if p.name in MANIFEST_NAMES)


def build_index(manifests):
    idx, docket_crime = {}, {}
    for m in manifests:
        n = 0
        for rec in load_records(m):
            if not isinstance(rec, dict):
                continue
            keys = set()
            cd = rec.get("chosen_document") if isinstance(rec.get("chosen_document"), dict) else {}
            dl = rec.get("download") if isinstance(rec.get("download"), dict) else {}
            vals = [rec.get(k) for k in PATH_KEYS + URL_KEYS]
            vals += [cd.get(k) for k in ("filepath_local", "download_url")]
            vals += [dl.get(k) for k in ("path", "source_url")]
            for v in vals:
                if isinstance(v, str) and v:
                    b = basename_of(v)
                    if b:
                        keys.add(b.lower())
                        keys.add(Path(b).stem.lower())
            # PDFs saved as <document id>.pdf
            for v in (rec.get("document_id"), cd.get("id")):
                if v not in (None, ""):
                    keys.add(str(v).strip().lower())
            entry = {
                "doc_type_raw": first(rec, TYPE_KEYS),
                "url": pick_url(rec),
                "crime": first(rec, CRIME_KEYS) or (
                    crime_from_query(rec.get("query"))
                    if m.name == "recap_links.jsonl" else None),
                "manifest": m.name,
            }
            for k in keys:
                old = idx.get(k)
                if old is None:
                    idx[k] = dict(entry)
                else:  # same PDF in several manifests: fill in what's missing
                    for f in ("doc_type_raw", "url", "crime"):
                        if not old.get(f) and entry.get(f):
                            old[f] = entry[f]
                            if f == "crime":
                                old["manifest"] += "+" + entry["manifest"]
                dk = docket_key(k)
                if dk and entry["crime"]:
                    docket_crime.setdefault(dk, entry["crime"])
            n += 1
        print(f"  indexed {n:>6} records from {m}", file=sys.stderr)
    return idx, docket_crime


def peek_first_page(pdf):
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("--peek needs pypdf:  pip install pypdf")
    try:
        r = PdfReader(str(pdf))
        txt = (r.pages[0].extract_text() or "")[:2000] if r.pages else ""
    except Exception:
        return ""
    # Caption/title area only; strict_prefix off because captions start oddly
    return norm_doc_type(txt, strict_prefix=False)


def is_real_pdf(path):
    try:
        with open(path, "rb") as f:
            return f.read(1024).lstrip().startswith(b"%PDF")
    except OSError:
        return False


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


GOOD = set(DOC_TYPES)


def merge_duplicates(rows):
    """Keep one row per identical file; pool labels across its copies."""
    groups = {}
    for r in rows:
        if r.get("real_pdf", True):  # identical junk files are not the same document
            groups.setdefault(r["sha256"], []).append(r)
    n_groups = n_dups = 0
    for g in groups.values():
        if len(g) < 2:
            continue
        n_groups += 1
        g.sort(key=lambda r: (bool(r["reasons"]), not r["url"],
                              r["doc_type"] not in GOOD, not r["hit"], r["path"]))
        keep, others = g[0], g[1:]
        n_dups += len(others)
        for o in others:
            o["duplicate_of"] = keep["path"]
        # fill gaps in the kept row from its copies
        if not keep["url"]:
            for o in others:
                if o["url"]:
                    keep["url"] = o["url"]
                    keep["reasons"] = [x for x in keep["reasons"] if x != "no_url"]
                    break
        if keep["doc_type"] not in GOOD:
            types = {o["doc_type"] for o in others if o["doc_type"] in GOOD}
            if len(types) == 1:
                keep["doc_type"] = types.pop()
                keep["label_origin"] += "+copy"
                keep["reasons"] = [x for x in keep["reasons"]
                                   if x not in ("no_doc_type", "not_one_of_8_types")]
        if not keep["crime_type"]:
            for o in others:
                if o["crime_type"]:
                    keep["crime_type"] = o["crime_type"]
                    keep["reasons"] = [x for x in keep["reasons"] if x != "no_crime"]
                    break
        # Copies whose labels disagree. A generic label loses to a specific one:
        #   plain "trafficking" vs forced_labor / labor_trafficking / sex_trafficking
        #   indictment vs superseding_indictment
        crimes = {r["crime_type"] for r in g if r["crime_type"]}
        if len(crimes) > 1 and "trafficking" in crimes:
            specific = crimes - {"trafficking"}
            if len(specific) == 1 and next(iter(specific)).endswith(
                    ("trafficking", "labor")):
                if keep["crime_type"] != next(iter(specific)):
                    keep["crime_type"] = next(iter(specific))
                    keep["label_origin"] += "+copy_crime"
                crimes = specific
        if len(crimes) > 1:
            keep["reasons"].append("copies_disagree_crime=" + "|".join(sorted(crimes)))
        types = {r["doc_type"] for r in g if r["doc_type"] in GOOD}
        if types == {"indictment", "superseding_indictment"}:
            if keep["doc_type"] != "superseding_indictment":
                keep["doc_type"] = "superseding_indictment"
                keep["label_origin"] += "+copy_type"
            types = {"superseding_indictment"}
        if len(types) > 1:
            keep["reasons"].append("copies_disagree_type=" + "|".join(sorted(types)))
    return n_groups, n_dups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("court_pdf_labels.csv"))
    ap.add_argument("--inspect", action="store_true",
                    help="print manifest field names and exit")
    ap.add_argument("--crime-field", default="",
                    help="field in recap_links.jsonl that holds the crime")
    ap.add_argument("--peek", action="store_true",
                    help="fallback: classify unlabeled PDFs from first-page text (local only)")
    a = ap.parse_args()
    root = a.root.resolve()

    manifests = find_manifests(root)
    if a.inspect:
        for m in manifests:
            c, nested = Counter(), {}
            for i, rec in enumerate(load_records(m)):
                if isinstance(rec, dict):
                    c.update(rec.keys())
                    for k, v in rec.items():
                        if isinstance(v, dict):
                            nested.setdefault(k, set()).update(v.keys())
                        elif isinstance(v, list) and v and isinstance(v[0], dict):
                            nested.setdefault(k + "[]", set()).update(v[0].keys())
                if i >= 500:
                    break
            print(f"{m.relative_to(root)}: {sorted(c)}")
            for k in sorted(nested):
                print(f"    {k} -> {sorted(nested[k])}")
        return

    if a.crime_field:
        CRIME_KEYS.insert(0, a.crime_field)
    idx, docket_crime = build_index(manifests)
    pdfs = sorted(p for p in root.rglob("*") if p.suffix.lower() == ".pdf")
    stats = Counter()

    rows = []
    if True:
        for pdf in pdfs:
            rel = pdf.relative_to(root).as_posix()
            parts = rel.split("/")
            # Works whether --root is the recap folder or the folder above it
            sub = parts[1:] if parts and parts[0].lower() == "recap" else parts
            in_bulk = bool(sub) and sub[0].lower() == "bulk"
            hit = idx.get(pdf.name.lower()) or idx.get(pdf.stem.lower())

            doc_type, url, origin, reasons = "", "", "", []
            if hit:
                doc_type = norm_doc_type(hit["doc_type_raw"])
                url = hit["url"]
                origin = hit["manifest"]
            ft = norm_doc_type(pdf.stem)
            if not doc_type or doc_type == "other":
                if ft != "other":
                    doc_type, origin = ft, (origin + "+filename").lstrip("+")
            elif ft not in ("other", doc_type):
                if doc_type == "indictment" and ft == "superseding_indictment":
                    doc_type, origin = ft, origin + "+filename"
                else:
                    reasons.append(f"manifest={doc_type}/filename={ft}")
            if not doc_type and a.peek:  # never override a manifest's "other"
                pt = peek_first_page(pdf)
                if pt and pt != "other":
                    doc_type, origin = pt, (origin + "+firstpage").lstrip("+")
            if not url:
                url = url_from_recap_name(pdf.name)
            if not origin:
                origin = "unresolved"

            if in_bulk:
                crime = (hit or {}).get("crime") or ""
                if not crime and docket_crime.get(docket_key(pdf.name)):
                    crime = docket_crime[docket_key(pdf.name)]
                    reasons.append("crime_from_same_docket")
            else:
                crime = sub[0] if len(sub) > 1 else ""
            crime = str(crime).strip().lower().replace(" ", "_")

            real_pdf = is_real_pdf(pdf)
            if not real_pdf: reasons.append("not_a_pdf")
            if not doc_type: reasons.append("no_doc_type")
            elif doc_type == "other": reasons.append("not_one_of_8_types")
            if not crime: reasons.append("no_crime")
            if not url: reasons.append("no_url")
            rows.append({"path": rel, "crime_type": crime,
                         "doc_type": doc_type or "", "source": "recap",
                         "url": url, "label_origin": origin,
                         "reasons": reasons, "sha256": file_hash(pdf),
                         "real_pdf": real_pdf,
                         "duplicate_of": "", "hit": bool(hit)})

    dup_groups, dup_files = merge_duplicates(rows)

    cols = ["path", "crime_type", "doc_type", "source", "url",
            "label_origin", "needs_review", "sha256", "duplicate_of"]
    with a.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r["path"], r["crime_type"], r["doc_type"], r["source"],
                        r["url"], r["label_origin"], ";".join(r["reasons"]),
                        r["sha256"], r["duplicate_of"]])
            stats["total"] += 1
            stats["from_manifest" if r["hit"] else "no_manifest_match"] += 1
            if r["duplicate_of"]:
                continue
            stats["unique_pdfs"] += 1
            stats[f"type:{r['doc_type'] or 'blank'}"] += 1
            if r["reasons"]:
                stats["needs_review (unique)"] += 1
            if not r.get("real_pdf", True):
                stats["not_a_pdf"] += 1
    stats["duplicate_copies"] = dup_files
    stats["duplicate_groups"] = dup_groups

    print(f"\nwrote {a.out}", file=sys.stderr)
    for k, v in sorted(stats.items()):
        print(f"  {k:<32}{v}", file=sys.stderr)


if __name__ == "__main__":
    main()
