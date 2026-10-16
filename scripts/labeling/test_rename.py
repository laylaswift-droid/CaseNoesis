import csv
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "rename_court_pdfs.py"
FIELDS = ["path", "crime_type", "doc_type", "source", "url",
          "label_origin", "needs_review", "sha256", "duplicate_of"]


class RenameSafety(unittest.TestCase):
    def setUp(self):
        # A pretend dataset in a temporary folder. Nothing real is used.
        self.tmp = Path(tempfile.mkdtemp(prefix="rename_test_"))
        self.root = self.tmp / "recap"
        (self.root / "fraud").mkdir(parents=True)
        (self.root / "fraud" / "a.pdf").write_bytes(b"%PDF-1.4 pretend court file")
        (self.tmp / "private").mkdir()
        (self.tmp / "private" / "secret.txt").write_text("PRIVATE NOTE, NOT A COURT RECORD")
        self.out = self.tmp / "release"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_rename(self, rows):
        labels = self.tmp / "labels.csv"
        with labels.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({**{k: "" for k in FIELDS}, **row})
        subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root),
                        "--labels", str(labels), "--out", str(self.out)],
                       capture_output=True, text=True)

    def good_row(self, **changes):
        row = {"path": "fraud/a.pdf", "crime_type": "fraud",
               "doc_type": "indictment", "source": "recap"}
        row.update(changes)
        return row

    def test_normal_row_is_copied(self):
        self.run_rename([self.good_row()])
        self.assertTrue((self.out / "fraud_indictment_recap_0001.pdf").is_file())

    def test_file_outside_dataset_is_not_copied(self):
        self.run_rename([self.good_row(path="../private/secret.txt", doc_type="plea")])
        leaked = [p.name for p in self.out.glob("*.pdf") if b"PRIVATE" in p.read_bytes()]
        self.assertEqual(leaked, [], f"a file from outside the dataset was released as {leaked}")

    def test_crime_type_cannot_write_outside_release(self):
        self.run_rename([self.good_row(crime_type="../../escaped", doc_type="judgment")])
        escaped = [str(p) for p in self.tmp.parent.glob("escaped*")] + \
                  [str(p) for p in self.tmp.glob("escaped*")]
        for p in escaped:
            Path(p).unlink()  # tidy up anything that escaped
        self.assertEqual(escaped, [], f"a file was written outside the release folder: {escaped}")

    def test_good_rows_still_copied_alongside_bad_ones(self):
        self.run_rename([self.good_row(),
                         self.good_row(path="../private/secret.txt", doc_type="plea")])
        self.assertTrue((self.out / "fraud_indictment_recap_0001.pdf").is_file())

    def test_linked_file_inside_dataset_is_still_copied(self):
        # Some dataset files are links to files stored elsewhere. They must still be copied.
        elsewhere = self.tmp / "storage" / "real.pdf"
        elsewhere.parent.mkdir()
        elsewhere.write_bytes(b"%PDF-1.4 linked court file")
        try:
            os.symlink(elsewhere, self.root / "fraud" / "linked.pdf")
        except (OSError, NotImplementedError):
            self.skipTest("this computer does not allow creating links")
        self.run_rename([self.good_row(path="fraud/linked.pdf")])
        self.assertTrue((self.out / "fraud_indictment_recap_0001.pdf").is_file(),
                        "a linked file inside the dataset was refused")


if __name__ == "__main__":
    unittest.main(verbosity=2)