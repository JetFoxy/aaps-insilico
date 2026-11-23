import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_whatif_page_wires_assets_and_labels():
    html = (ROOT / "web/whatif.html").read_text(encoding="utf-8")
    assert 'src="whatif.js' in html
    assert 'href="whatif.css' in html
    assert 'data-i18n="whatif.connect"' in html
    assert 'id="variant-list"' in html
    assert 'id="chart"' in html
    assert 'data-i18n="whatif.mode.replay"' in html
    assert 'data-i18n="whatif.mode.model"' in html
    assert 'data-i18n="whatif.variants"' in html
    assert 'data-i18n="whatif.warning.notPrediction"' in html


def test_i18n_has_whatif_keys_in_both_languages():
    text = (ROOT / "web/i18n.js").read_text(encoding="utf-8")
    for key in (
        "whatif.connect",
        "whatif.mode.replay",
        "whatif.mode.model",
        "whatif.variants",
        "whatif.warning.notPrediction",
        "whatif.legend.bolus",
        "whatif.metrics.realDay",
        "whatif.notReproduced.parity",
        "whatif.notReproduced.modelAbsolute",
    ):
        assert text.count(f'"{key}"') >= 2
