"""Unit tests for TextAnalyzer."""
import pytest
from text_analyzer import TextAnalyzer


@pytest.fixture(scope='module')
def analyzer():
    return TextAnalyzer()


class TestTokenize:
    def test_basic_english(self, analyzer):
        assert analyzer.tokenize('Hello world') == ['hello', 'world']

    def test_basic_russian(self, analyzer):
        tokens = analyzer.tokenize('Привет мир')
        assert tokens == ['привет', 'мир']

    def test_numbers_included(self, analyzer):
        assert '42' in analyzer.tokenize('There are 42 items')

    def test_punctuation_stripped(self, analyzer):
        tokens = analyzer.tokenize('Hello, world!')
        assert tokens == ['hello', 'world']

    def test_empty_string(self, analyzer):
        assert analyzer.tokenize('') == []


class TestLemmatize:
    def test_returns_list(self, analyzer):
        lemmas = analyzer.lemmatize(['бегу', 'бежишь'])
        assert isinstance(lemmas, list)
        assert len(lemmas) == 2

    def test_russian_verb_lemma(self, analyzer):
        # 'кота' and 'котом' are both forms of 'кот'
        lemmas = analyzer.lemmatize(['кота', 'котом'])
        assert lemmas[0] == lemmas[1]

    def test_empty_list(self, analyzer):
        assert analyzer.lemmatize([]) == []


class TestRemoveStopwords:
    def test_removes_russian_stopwords(self, analyzer):
        tokens = ['кот', 'и', 'собака', 'в', 'доме']
        result = analyzer.remove_stopwords(tokens)
        assert 'и' not in result
        assert 'в' not in result
        assert 'кот' in result

    def test_empty_list(self, analyzer):
        assert analyzer.remove_stopwords([]) == []


class TestGetFrequencyDistribution:
    def test_counts_correctly(self, analyzer):
        tokens = ['кот', 'кот', 'собака']
        freq = analyzer.get_frequency_distribution(tokens)
        assert freq['кот'] == 2
        assert freq['собака'] == 1

    def test_sorted_by_frequency(self, analyzer):
        tokens = ['a'] * 3 + ['b'] * 5 + ['c']
        freq = analyzer.get_frequency_distribution(tokens)
        values = list(freq.values())
        assert values == sorted(values, reverse=True)

    def test_empty_list(self, analyzer):
        assert analyzer.get_frequency_distribution([]) == {}


class TestGetTextStats:
    def test_basic_stats(self, analyzer):
        s = analyzer.get_text_stats('Hello world. Goodbye world.')
        assert s['total_words'] == 4
        assert s['unique_words'] == 3
        assert s['sentences'] == 2

    def test_avg_word_length(self, analyzer):
        s = analyzer.get_text_stats('ab abcd')
        # 2 and 4 → avg 3.0
        assert s['avg_word_length'] == pytest.approx(3.0)

    def test_lexical_diversity(self, analyzer):
        s = analyzer.get_text_stats('one one one')
        assert s['lexical_diversity'] == pytest.approx(1 / 3)

    def test_empty_text(self, analyzer):
        s = analyzer.get_text_stats('')
        assert s['total_words'] == 0
        assert s['avg_word_length'] == 0
        assert s['lexical_diversity'] == 0


class TestAnalyze:
    def test_returns_required_keys(self, analyzer):
        result = analyzer.analyze('Кот сидит на крыше.')
        for key in ('stats', 'frequency', 'tokens_count', 'lemmas_count'):
            assert key in result

    def test_tokens_count(self, analyzer):
        result = analyzer.analyze('раз два три')
        assert result['tokens_count'] == 3

    def test_lemmas_count(self, analyzer):
        # 'кота' and 'котом' are both forms of 'кот' → one unique lemma
        result = analyzer.analyze('кота котом')
        assert result['lemmas_count'] == 1
