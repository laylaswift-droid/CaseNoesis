import unittest
from label_court_pdfs import norm_doc_type


class LabelsThatAreRight(unittest.TestCase):
    def test_correct_labels(self):
        cases = {
            "INDICTMENT as to John Doe (1) count(s) 1-5": "indictment",
            "SUPERSEDING INDICTMENT as to A, B": "superseding_indictment",
            "PLEA AGREEMENT re: count One of the Indictment": "plea",
            "Motion to Dismiss Indictment": "other",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(norm_doc_type(text), expected)


class LabelsThatAreWrong(unittest.TestCase):
    def test_entries_that_only_mention_a_filing(self):
        cases = {
            "Waiver of Indictment": "other",
            "Minute Entry for proceedings held: Change of Plea Hearing as to A": "other",
            "Minute Entry for Arraignment on Superseding Indictment": "other",
            "Docket Annotation": "other",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(norm_doc_type(text), expected,
                                 f"{text!r} was labeled {norm_doc_type(text)!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
