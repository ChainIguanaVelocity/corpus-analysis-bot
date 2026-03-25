import re
from collections import Counter
import pymorphy2

# Common Russian stopwords — replaces the nltk.corpus.stopwords dependency.
_RUSSIAN_STOPWORDS = {
    'и', 'в', 'во', 'не', 'что', 'он', 'на', 'я', 'с', 'со', 'как', 'а',
    'то', 'все', 'она', 'так', 'его', 'но', 'да', 'ты', 'к', 'у', 'же',
    'вы', 'за', 'бы', 'по', 'только', 'ее', 'мне', 'было', 'вот', 'от',
    'меня', 'еще', 'нет', 'о', 'из', 'ему', 'теперь', 'когда', 'даже',
    'ну', 'вдруг', 'ли', 'если', 'уже', 'или', 'ни', 'быть', 'был', 'него',
    'до', 'вас', 'нибудь', 'опять', 'уж', 'вам', 'ведь', 'там', 'потом',
    'себя', 'ничего', 'ей', 'может', 'они', 'тут', 'где', 'есть', 'надо',
    'ней', 'для', 'мы', 'тебя', 'их', 'чем', 'была', 'сам', 'чтоб', 'без',
    'будто', 'человек', 'чего', 'раз', 'тоже', 'себе', 'под', 'будет',
    'ж', 'тогда', 'кто', 'этот', 'того', 'потому', 'этого', 'какой',
    'совсем', 'ним', 'здесь', 'этом', 'один', 'почти', 'мой', 'тем',
    'чтобы', 'нее', 'сейчас', 'были', 'куда', 'зачем', 'всех', 'никогда',
    'можно', 'при', 'наконец', 'два', 'об', 'другой', 'хоть', 'после',
    'над', 'больше', 'тот', 'через', 'эти', 'нас', 'про', 'всего', 'них',
    'какая', 'много', 'разве', 'три', 'эту', 'моя', 'впрочем', 'хорошо',
    'свою', 'этой', 'перед', 'иногда', 'лучше', 'чуть', 'том', 'нельзя',
    'такой', 'им', 'более', 'всегда', 'конечно', 'всю', 'между',
}

# Sentence boundary: split on . ! ? followed by whitespace or end-of-string.
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')
# Token: sequences of Unicode letters or digits.
_TOKEN_RE = re.compile(r'[^\W\d_]+|\d+', re.UNICODE)


class TextAnalyzer:
    def __init__(self):
        self.morph = pymorphy2.MorphAnalyzer()
        self.stop_words = _RUSSIAN_STOPWORDS

    def tokenize(self, text):
        return _TOKEN_RE.findall(text.lower())

    def lemmatize(self, tokens):
        return [self.morph.parse(token)[0].normal_form for token in tokens]

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
