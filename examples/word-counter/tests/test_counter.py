import unittest

from counter import count_words, most_common


class CountWords(unittest.TestCase):
    def test_spaces(self):
        self.assertEqual(count_words("one two three"), 3)

    def test_tabs_and_newlines(self):
        self.assertEqual(count_words("one\ttwo\nthree"), 3)


class MostCommon(unittest.TestCase):
    def test_case_and_punctuation(self):
        self.assertEqual(most_common("The cat. the dog, THE end"), "the")

    def test_empty(self):
        self.assertIsNone(most_common("   "))


if __name__ == "__main__":
    unittest.main()
