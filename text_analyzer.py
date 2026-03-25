import re
from collections import Counter

# Common Ossetian (Iron dialect) stopwords — function words, pronouns,
# particles, conjunctions, and frequent auxiliary verb forms.
_OSSETIAN_STOPWORDS = {
    # Personal pronouns
    'æз', 'ды', 'уый', 'мах', 'сымах', 'уыдон',
    # Possessive particles / short pronouns
    'мæ', 'дæ', 'йæ', 'нæ', 'уæ', 'сæ', 'мæн', 'дæу',
    # Conjunctions and discourse particles
    'æмæ', 'æви', 'фæлæ', 'уæдта', 'уæд', 'æрмæстдæр',
    # Temporal / conditional conjunctions
    'куы', 'куыд', 'кæд', 'амæ',
    # Demonstratives and determiners
    'уыцы', 'ацы', 'иу', 'иуæй',
    # Negation
    'ма', 'нæй',
    # Common copula and auxiliary forms
    'у', 'сты', 'уыд', 'уыдысты', 'уа', 'уой',
    # Adverbs of place and time
    'ам', 'уым', 'ныр', 'æрмæст', 'дæр', 'та',
    # Postpositions / case-like particles
    'æрдæм', 'æхсæн', 'фæстæ', 'размæ', 'хуызæн',
    # Other high-frequency function words
    'цы', 'чи', 'кæй', 'кæм', 'кæцæй',
    'æй', 'ын', 'ыл', 'æнæ', 'дзы',
    'уæм', 'сæм', 'ыф', 'æм',
}

# Sentence boundary: split on . ! ? followed by whitespace or end-of-string.
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')
# Token: sequences of Unicode letters (including æ / Æ) or digits.
_TOKEN_RE = re.compile(r'[^\W\d_]+|\d+', re.UNICODE)


class TextAnalyzer:
    def __init__(self):
        self.stop_words = _OSSETIAN_STOPWORDS

    def tokenize(self, text):
        return _TOKEN_RE.findall(text.lower())

    def lemmatize(self, tokens):
        # Ossetian morphological resources are not available via pymorphy2;
        # return each token in its lowercase form as a stand-in for the lemma.
        return list(tokens)

    def remove_stopwords(self, tokens):
        return [token for token in tokens if token not in self.stop_words]

    def get_frequency_distribution(self, tokens):
        return dict(Counter(tokens).most_common(50))

    def get_text_stats(self, text):
        sentences = [s for s in _SENTENCE_RE.split(text.strip()) if s]
        tokens = self.tokenize(text)
        return {
            'total_words': len(tokens),
            'unique_words': len(set(tokens)),
            'sentences': len(sentences),
            'avg_word_length': sum(len(w) for w in tokens) / len(tokens) if tokens else 0,
            'lexical_diversity': len(set(tokens)) / len(tokens) if tokens else 0,
        }

    def analyze(self, text):
        tokens = self.tokenize(text)
        lemmas = self.lemmatize(tokens)
        cleaned = self.remove_stopwords(lemmas)
        return {
            'stats': self.get_text_stats(text),
            'frequency': self.get_frequency_distribution(cleaned),
            'tokens_count': len(tokens),
            'lemmas_count': len(set(lemmas)),
        }
