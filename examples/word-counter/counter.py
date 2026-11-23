"""Count words and the most common word in a piece of text."""
from collections import Counter


def count_words(text):
    """Number of words, split on any whitespace (spaces, tabs, newlines)."""
    return len(text.split())


def most_common(text):
    """The most frequent word, lowercased, or None for empty text."""
    words = [w.lower().strip(".,;:!?\"'()") for w in text.split()]
    words = [w for w in words if w]
    if not words:
        return None
    return Counter(words).most_common(1)[0][0]
