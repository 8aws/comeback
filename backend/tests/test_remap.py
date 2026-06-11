"""Path remapping for cross-host migrations."""
from app.restore.manager import _remap_path


def test_no_map_returns_original():
    assert _remap_path("/share/Container/app", None) == "/share/Container/app"
    assert _remap_path("/share/Container/app", {}) == "/share/Container/app"


def test_prefix_remap():
    m = {"/share/Container": "/DATA/AppData"}
    assert _remap_path("/share/Container/app/data", m) == "/DATA/AppData/app/data"


def test_exact_match_remap():
    m = {"/share/Container/app": "/DATA/AppData/app"}
    assert _remap_path("/share/Container/app", m) == "/DATA/AppData/app"


def test_no_partial_component_match():
    # /share/Container2 must NOT match the /share/Container prefix
    m = {"/share/Container": "/DATA/AppData"}
    assert _remap_path("/share/Container2/app", m) == "/share/Container2/app"


def test_longest_prefix_wins():
    m = {"/share": "/mnt", "/share/Container": "/DATA/AppData"}
    assert _remap_path("/share/Container/x", m) == "/DATA/AppData/x"
    assert _remap_path("/share/other", m) == "/mnt/other"


def test_trailing_slashes_normalised():
    m = {"/share/Container/": "/DATA/AppData/"}
    assert _remap_path("/share/Container/app", m) == "/DATA/AppData/app"


def test_unrelated_path_untouched():
    m = {"/share/Container": "/DATA/AppData"}
    assert _remap_path("/opt/data", m) == "/opt/data"
