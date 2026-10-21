import re


_TOKEN = re.compile(r"[a-z0-9]+")


def variants(word):

    forms = {word}

    if len(word) > 3 and word.endswith("ies"):
        forms.add(word[:-3] + "y")

    if len(word) > 4 and word.endswith("es"):
        forms.add(word[:-2])

    if len(word) > 3 and word.endswith("s"):
        forms.add(word[:-1])

    return forms


def words(text):

    return {
        token
        for token in _TOKEN.findall(text.lower())
        if len(token) > 2
    }


def matches(word, other_words):

    forms = variants(word)

    return any(forms & variants(other) for other in other_words)


def overlap(first_words, second_words):
    """How many words of the first set appear (in any plural form) in the second."""

    return sum(1 for word in first_words if matches(word, second_words))
