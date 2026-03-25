import os
import tempfile
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from wordcloud import WordCloud
import pandas as pd


class DataVisualizer:
    def __init__(self, data=None):
        self.data = data

    def plot_frequency_distribution(self, freq_dict, title='Word Frequency Distribution'):
        """Plot top-N word frequencies and save to a temp file. Returns file path."""
        top = dict(list(sorted(freq_dict.items(), key=lambda x: x[1], reverse=True))[:20])
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.bar(top.keys(), top.values(), color='steelblue')
        ax.set_title(title)
        ax.set_xlabel('Word')
        ax.set_ylabel('Frequency')
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        path = _save_figure(fig)
        plt.close(fig)
        return path

    def plot_word_cloud(self, freq_dict_or_text, title='Word Cloud'):
        """Generate a word cloud image and save to a temp file. Returns file path.

        Accepts either a frequency dict {word: count} or a plain text string.
        """
        if isinstance(freq_dict_or_text, dict):
            wordcloud = WordCloud(
                width=800,
                height=400,
                background_color='white',
                max_words=200,
            ).generate_from_frequencies(freq_dict_or_text)
        else:
            wordcloud = WordCloud(
                width=800,
                height=400,
                background_color='white',
                max_words=200,
            ).generate(freq_dict_or_text)
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.imshow(wordcloud, interpolation='bilinear')
        ax.axis('off')
        ax.set_title(title)
        plt.tight_layout()
        path = _save_figure(fig)
        plt.close(fig)
        return path

    def text_statistics(self, column):
        text = ' '.join(self.data[column].dropna().astype(str).tolist())
        num_words = len(text.split())
        num_unique_words = len(set(text.split()))
        return {'Total Words': num_words, 'Unique Words': num_unique_words}


def _save_figure(fig):
    """Save a matplotlib figure to a temporary PNG file and return its path."""
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    fig.savefig(tmp.name, format='png', bbox_inches='tight')
    tmp.close()
    return tmp.name
