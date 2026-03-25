"""Unit tests for DataVisualizer."""
import os
import pytest
from visualizer import DataVisualizer


@pytest.fixture(scope='module')
def vis():
    return DataVisualizer(None)


SAMPLE_FREQ = {'кот': 10, 'собака': 7, 'дом': 5, 'лес': 3, 'мир': 2}


class TestPlotFrequencyDistribution:
    def test_returns_existing_png(self, vis):
        path = vis.plot_frequency_distribution(SAMPLE_FREQ)
        assert os.path.exists(path)
        assert path.endswith('.png')
        os.unlink(path)

    def test_custom_title(self, vis):
        path = vis.plot_frequency_distribution(SAMPLE_FREQ, title='My Chart')
        assert os.path.exists(path)
        os.unlink(path)

    def test_single_word(self, vis):
        path = vis.plot_frequency_distribution({'слово': 1})
        assert os.path.exists(path)
        os.unlink(path)


class TestPlotWordCloud:
    def test_returns_existing_png_from_dict(self, vis):
        path = vis.plot_word_cloud(SAMPLE_FREQ)
        assert os.path.exists(path)
        assert path.endswith('.png')
        os.unlink(path)

    def test_returns_existing_png_from_text(self, vis):
        path = vis.plot_word_cloud('кот кот собака дом лес мир')
        assert os.path.exists(path)
        os.unlink(path)
