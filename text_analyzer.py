import nltk
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize, sent_tokenize
from nltk.probability import FreqDist
import pymorphy2
from collections import Counter
import re


def _ensure_nltk_data():
    """Download required NLTK datasets only if not already present."""
    for resource, path in [
        ('punkt', 'tokenizers/punkt'),
        ('punkt_tab', 'tokenizers/punkt_tab'),
        ('stopwords', 'corpora/stopwords'),
    ]:
        try:
            nltk.data.find(path)
        except LookupError:
            nltk.download(resource, quiet=True)


_ensure_nltk_data()


class TextAnalyzer:
    def __init__(self):
        self.morph = pymorphy2.MorphAnalyzer()
        self.stop_words = set(stopwords.words('russian'))

    def tokenize(self, text):
        tokens = word_tokenize(text.lower())
        return [token for token in tokens if token.isalnum()]

    def lemmatize(self, tokens):
        lemmas = []
        for token in tokens:
            parsed = self.morph.parse(token)[0]
            lemmas.append(parsed.normal_form)
        return lemmas

    def remove_stopwords(self, tokens):
        return [token for token in tokens if token not in self.stop_words]

    def get_frequency_distribution(self, tokens):
        freq_dist = FreqDist(tokens)
        return dict(freq_dist.most_common(50))

    def get_text_stats(self, text):
        sentences = sent_tokenize(text)
        tokens = self.tokenize(text)
        words = self.remove_stopwords(tokens)
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
