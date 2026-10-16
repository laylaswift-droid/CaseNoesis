# Court PDF labeling

Two scripts that label every collected court PDF by crime type, document type and source, then copy the labeled files into a release folder under label-based names (e.g. `fraud_superseding_indictment_recap_0001.pdf`).

Both run locally on the dataset. They do not upload anything, and no court records are stored in this repo.

## 1. Label: `label_court_pdfs.py`

```
python label_court_pdfs.py --root <path to recap folder> --peek
```

Writes `court_pdf_labels.csv`, one row per PDF:

| Column | Meaning |
|---|---|
| `path` | PDF location under `--root` |
| `crime_type` | Crime folder name (`recap/fraud/` → `fraud`). For `recap/bulk/`, the crime phrase from the `query` field of `recap_links.jsonl` |
| `doc_type` | One of: indictment, superseding_indictment, plea, sentencing_memorandum, complaint, judgment, transcript, docket. Filings outside these are `other` |
| `source` | `recap` |
| `url` | CourtListener URL |
| `label_origin` | Where the labels came from (manifest name, `filename`, `firstpage`, `copy_*`) |
| `needs_review` | Blank if fully labeled; otherwise the reasons |
| `sha256` | File fingerprint |
| `duplicate_of` | For an exact copy of another PDF, the path of the copy that is kept |

How labels are found, in order:

1. **Manifests** next to the folders: `court_manifest.json`, `fraud/from_press.jsonl`, `fraud/fraud_study.jsonl`, `bulk/manifests/recap_links.jsonl`. PDFs are matched by file name, URL, or document ID. `press_lookup.jsonl` is not used.
2. **The PDF's file name**, e.g. a RECAP name like `gov.uscourts.<court>.<case>.<doc>.<att>.pdf`, which also gives the storage URL.
3. **`--peek` (optional)**: keyword match on the first page's text, only for PDFs with no type from steps 1–2. Runs locally; the text is never printed or saved.

A manifest description that only mentions a document type ("Motion to Dismiss Indictment") is labeled `other`, not `indictment`. The filing's own name at the start of a description wins over words later in it (attachment lists, hearing notes).

Files that are not real PDFs (empty files, saved error pages) are flagged `not_a_pdf` and are never treated as duplicates of each other. Local disk paths in a manifest are never used as URLs.

**Duplicates:** identical files (same `sha256`) keep one row each, but all except one are marked `duplicate_of`. Labels are pooled across copies. When copies disagree, a specific label wins over a generic one (`forced_labor` over `trafficking`, `superseding_indictment` over `indictment`); any other disagreement is flagged in `needs_review`.

`--inspect` prints the field names in each manifest (no record contents), useful if a manifest format changes.

## 2. Rename: `rename_court_pdfs.py`

```
python rename_court_pdfs.py --root <same recap folder> --out <new empty folder> --dry-run
python rename_court_pdfs.py --root <same recap folder> --out <new empty folder>
```

Copies each fully labeled, non-duplicate PDF into `--out` as `<crime>_<doc_type>_<source>_<NNNN>.pdf`. Numbering restarts per group and follows original path order, so reruns produce the same names. Originals are never changed. `--out` must be a new folder outside `--root`.

Also writes into `--out`:

- `release_manifest.csv`: new name → labels, URL, original path, sha256
- `unlabeled.csv`: PDFs still needing review, with reasons
- `duplicates.csv`: exact copies that were skipped

## 3. Check against the collector: `check_against_collector.py`

```
python check_against_collector.py --root <same recap folder> --labels court_pdf_labels.csv
```

Prints counts only. Compares the labels with what the collector recorded: PDFs per folder vs the frozen snapshot in `data/collected/README.md` (820 PDFs), how many records in each manifest were matched, document type vs the collector's `document_kind` tags in `fraud_court_lookup.jsonl` (this file was removed from `main` on 7 Oct 2026; keep a local copy and pass `--fraud-lookup <path>`, otherwise this step is skipped), how duplicates spread across folders and cases, and whether every URL is a CourtListener link.

On the 264 public fraud filings, the labels agree with the collector's tags on 259 of 263 joined filings. The 4 differences are tags in the lookup that don't match the description (two plea agreements and two affidavits tagged as indictments).

Requires Python 3; `--peek` also needs `pip install pypdf`.
