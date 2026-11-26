from pathlib import Path

import pytest

from agent import i18n_layers


@pytest.fixture(autouse=True)
def super_agent_locales():
    # Direct unit tests construct the runtime without the plugin loader.
    directory = Path(__file__).resolve().parents[3] / "plugins/super_agent/locales"
    packs = [
        i18n_layers.register_pack(lang, surface, i18n_layers.parse_locale_file(path), source="super_agent")
        for lang, surface, path in i18n_layers.scan_locale_dir(directory)
    ]
    yield
    for pack in packs:
        i18n_layers.unregister_pack(pack)
