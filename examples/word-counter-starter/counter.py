"""Count words and the most common word in a piece of text."""
from collections import Counter


def count_words(text):
    """Number of words, split on any whitespace (spaces, tabs, newlines)."""
    return len(text.split())


def most_common(text):
    """The most frequent word, lowercased, or None for empty text."""
    raise NotImplementedError("Write this function.")
