"""Moving an installed connector onto a renamed backend host.

The product's domain changed. An installed connector cannot be repointed by the
installer -- an upgrade preserves ``connector.json`` on purpose, because that is
where the pairing lives -- so the connector rewrites the address itself the
first time it loads a config naming a host we have retired.

That rewrite is the only reason the old DNS record can ever be switched off: it
is what turns "every customer must be visited" into "every customer takes the
update they were going to take anyway".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tally_connector.config import RENAMED_HOSTS, load_settings, repointed_url, save_settings

OLD = "wss://api-tallyflow.theshubhanshu.dev/v1/connector"
NEW = "wss://api-tallyflow.jsrprimesolution.com/v1/connector"


def test_a_retired_host_is_rewritten_and_recorded(tmp_path: Path) -> None:
    path = tmp_path / "connector.json"
    save_settings({"connector_id": "abc", "connector_secret": "s", "backend_url": OLD}, path)

    settings = load_settings(path)

    assert settings.backend_url == NEW
    # Written back, not merely corrected in memory: the update that carries this
    # build restarts the service, and a rewrite that did not persist would be
    # redone on every start and lost the moment the map is finally deleted.
    assert json.loads(path.read_text(encoding="utf-8"))["backend_url"] == NEW


def test_the_pairing_survives_the_rewrite(tmp_path: Path) -> None:
    # The whole point is that this is not a re-pair. A connector that lost its
    # credentials here would come back as a second row in the customer's app.
    path = tmp_path / "connector.json"
    save_settings(
        {"connector_id": "abc", "connector_secret": "s", "backend_url": OLD, "tally_port": 9001},
        path,
    )

    settings = load_settings(path)

    assert settings.connector_id == "abc"
    assert settings.connector_secret == "s"
    assert settings.tally_port == 9001


def test_the_manifest_follows_the_new_host(tmp_path: Path) -> None:
    # Updates are fetched from the host in backend_url. If this still named the
    # old one, a repointed connector would go on asking a retired name for its
    # updates and would be stranded on this build.
    path = tmp_path / "connector.json"
    save_settings({"backend_url": OLD}, path)

    assert "jsrprimesolution.com" in load_settings(path).manifest_url


def test_an_untouched_host_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "connector.json"
    save_settings({"backend_url": "wss://tally.example.com/v1/connector"}, path)

    assert load_settings(path).backend_url == "wss://tally.example.com/v1/connector"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Scheme and path are the operator's. A support engineer pointed at a
        # plaintext test backend, or at a non-standard path, meant it.
        ("ws://api-tallyflow.theshubhanshu.dev/v1/connector", "ws://"),
        ("wss://api-tallyflow.theshubhanshu.dev:8443/v1/connector", ":8443"),
        ("wss://api-tallyflow.theshubhanshu.dev/custom/path", "/custom/path"),
    ],
)
def test_only_the_host_is_replaced(url: str, expected: str) -> None:
    moved = repointed_url(url)

    assert moved is not None
    assert expected in moved
    assert "jsrprimesolution.com" in moved


def test_a_url_that_is_not_a_url_is_not_a_crash() -> None:
    # backend_url is validated when settings are built, not when the file is
    # read, so a hand-edited config reaches this code as whatever was typed.
    assert repointed_url("") is None
    assert repointed_url("not a url") is None


def test_every_new_name_is_itself_settled() -> None:
    # A rename whose target is also in the map would move a connector twice --
    # once per start, never settling -- and the second hop would be invisible.
    assert not set(RENAMED_HOSTS.values()) & set(RENAMED_HOSTS)
