"""Bind-mount source normalisation in compose deploys."""
from app.deploy.compose import _normalize_volume


def test_missing_leading_slash_is_fixed():
    # the exact case from the reported deploy error
    assert _normalize_volume("media/HDD-Storage/AppData/webserver/loywallet:/var/www/html") \
        == "/media/HDD-Storage/AppData/webserver/loywallet:/var/www/html"


def test_absolute_path_untouched():
    assert _normalize_volume("/share/Container/app:/data") == "/share/Container/app:/data"


def test_named_volume_untouched():
    assert _normalize_volume("apache_data:/var/www/html") == "apache_data:/var/www/html"


def test_named_volume_with_mode_untouched():
    assert _normalize_volume("db_data:/var/lib/mysql:rw") == "db_data:/var/lib/mysql:rw"


def test_relative_dot_path_untouched():
    # explicit ./ or ../ are left as-is (user's intent is unambiguous)
    assert _normalize_volume("./apache/html:/var/www/html") == "./apache/html:/var/www/html"
    assert _normalize_volume("../shared:/data") == "../shared:/data"


def test_home_path_untouched():
    assert _normalize_volume("~/data:/data") == "~/data:/data"


def test_mode_preserved_when_fixing():
    assert _normalize_volume("media/data:/data:ro") == "/media/data:/data:ro"


def test_single_token_untouched():
    # anonymous volume / just a container path — nothing to split
    assert _normalize_volume("/data") == "/data"
