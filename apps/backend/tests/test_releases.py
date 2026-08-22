"""The release catalogue and the per-request version check.

The update path is the one feature whose failure mode is worse than not working:
a floor published by mistake, or a manifest the backend cannot read but treats as
authoritative, locks paying customers out of their own accounting data. So most
of what is asserted here is about *refusing to block* -- the checks that keep a
missing file, a malformed entry, or a client that never identified itself from
turning into a fleet-wide outage.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from tally_backend.core.version_middleware import (
    ACTION_HEADER,
    LATEST_HEADER,
    MIN_HEADER,
    PLATFORM_HEADER,
    VERSION_HEADER,
)
from tally_backend.main import create_app
from tally_backend.services.releases import (
    ConnectorReleaseView,
    ReleaseCatalogue,
    UpdateAction,
)


def write_manifest(path: Path, **entries) -> Path:
    """Write a manifest shaped like the one `run.py publish` produces."""
    payload = {"schema": 1, "generated_at": "2026-08-16T19:24:31+00:00"}
    for platform, values in entries.items():
        payload[platform] = {
            "version": values.get("version", "0.2.0"),
            "file": f"TallyFlow-{values.get('version', '0.2.0')}.apk",
            "url": f"/downloads/TallyFlow-{values.get('version', '0.2.0')}.apk",
            "sha256": "a" * 64,
            "size_bytes": 23583706,
            "mandatory": values.get("mandatory", False),
            "min_supported_version": values.get("floor", "0.1.0"),
            "notes": values.get("notes", ""),
        }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------


def test_a_newer_build_is_optional_by_default(tmp_path) -> None:
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.2.0", "floor": "0.1.0"})
    catalogue = ReleaseCatalogue(manifest)

    verdict = catalogue.evaluate("android", "0.1.0")

    assert verdict.action is UpdateAction.OPTIONAL
    assert verdict.latest_version == "0.2.0"
    assert verdict.is_required is False


def test_being_below_the_floor_is_required(tmp_path) -> None:
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.3.0", "floor": "0.2.0"})
    catalogue = ReleaseCatalogue(manifest)

    assert catalogue.evaluate("android", "0.1.0").action is UpdateAction.REQUIRED


def test_a_mandatory_release_is_required_even_above_the_floor(tmp_path) -> None:
    """`mandatory` and the floor say different things and are checked separately."""
    manifest = write_manifest(
        tmp_path / "m.json",
        android={"version": "0.3.0", "floor": "0.1.0", "mandatory": True},
    )
    catalogue = ReleaseCatalogue(manifest)

    assert catalogue.evaluate("android", "0.2.0").action is UpdateAction.REQUIRED


def test_the_current_build_is_never_told_to_update(tmp_path) -> None:
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.2.0", "floor": "0.2.0"})
    catalogue = ReleaseCatalogue(manifest)

    verdict = catalogue.evaluate("android", "0.2.0")

    assert verdict.action is UpdateAction.NONE
    assert verdict.release is None


def test_a_build_ahead_of_the_manifest_is_left_alone(tmp_path) -> None:
    """A tester on an unreleased build must not be pushed backwards."""
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.2.0", "floor": "0.1.0"})
    catalogue = ReleaseCatalogue(manifest)

    assert catalogue.evaluate("android", "0.9.0").action is UpdateAction.NONE


def test_a_missing_manifest_blocks_nothing(tmp_path) -> None:
    """The single most damaging bug available here would be getting this wrong."""
    catalogue = ReleaseCatalogue(tmp_path / "does-not-exist.json")

    assert catalogue.evaluate("android", "0.0.1").action is UpdateAction.NONE
    assert catalogue.latest("android") is None


def test_a_malformed_manifest_blocks_nothing(tmp_path) -> None:
    path = tmp_path / "m.json"
    path.write_text("{ this is not json", encoding="utf-8")
    catalogue = ReleaseCatalogue(path)

    assert catalogue.evaluate("android", "0.0.1").action is UpdateAction.NONE


def test_one_broken_platform_does_not_take_the_other_down(tmp_path) -> None:
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "connector": {"no_version_here": True},
                "android": {"version": "0.2.0", "url": "/downloads/a.apk"},
            }
        ),
        encoding="utf-8",
    )
    catalogue = ReleaseCatalogue(path)

    assert catalogue.latest("connector") is None
    assert catalogue.latest("android") is not None


def test_a_client_that_did_not_say_its_version_is_left_alone(tmp_path) -> None:
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.9.0", "floor": "0.9.0"})
    catalogue = ReleaseCatalogue(manifest)

    assert catalogue.evaluate("android", "").action is UpdateAction.NONE


def test_a_manifest_with_no_floor_never_requires_anything(tmp_path) -> None:
    """An absent `min_supported_version` has to mean "no floor".

    Defaulting it to the published version instead would make every client below
    the newest one *required*, turning a routine release into a lockout.
    """
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {"android": {"version": "0.5.0", "url": "/downloads/a.apk", "sha256": "x"}}
        ),
        encoding="utf-8",
    )
    catalogue = ReleaseCatalogue(path)

    assert catalogue.evaluate("android", "0.1.0").action is UpdateAction.OPTIONAL


def test_a_republished_manifest_is_picked_up_without_a_restart(tmp_path) -> None:
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.2.0", "floor": "0.1.0"})
    catalogue = ReleaseCatalogue(manifest)
    assert catalogue.evaluate("android", "0.1.0").latest_version == "0.2.0"

    write_manifest(manifest, android={"version": "0.3.0", "floor": "0.1.0"})

    assert catalogue.evaluate("android", "0.1.0").latest_version == "0.3.0"


def test_an_unknown_platform_is_not_an_error(tmp_path) -> None:
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.2.0"})
    catalogue = ReleaseCatalogue(manifest)

    assert catalogue.evaluate("ios", "0.1.0").action is UpdateAction.NONE


# --------------------------------------------------------------------------
# The connector view
# --------------------------------------------------------------------------


def test_the_connector_view_reports_the_published_build(tmp_path) -> None:
    manifest = write_manifest(
        tmp_path / "m.json", connector={"version": "0.4.0", "floor": "0.2.0"}
    )
    view = ConnectorReleaseView(ReleaseCatalogue(manifest))

    assert view.latest_version == "0.4.0"
    assert view.min_version == "0.2.0"
    assert view.outdated("0.3.0") == ("0.4.0", False)
    assert view.outdated("0.1.0") == ("0.4.0", True)
    assert view.outdated("0.4.0") == ("", False)
    assert view.outdated("") == ("", False)


def test_the_connector_view_says_nothing_without_a_manifest(tmp_path) -> None:
    view = ConnectorReleaseView(ReleaseCatalogue(tmp_path / "absent.json"))

    assert view.latest_version == ""
    assert view.outdated("0.0.1") == ("", False)


# --------------------------------------------------------------------------
# The middleware, through the real app
# --------------------------------------------------------------------------


@asynccontextmanager
async def serving(settings, manifest: Path | None = None, **overrides):
    """A live client against the real app, with a manifest configured.

    Builds its own app rather than using the shared ``client`` fixture because
    every test here needs a different manifest, and the catalogue is constructed
    once per app.

    The lifespan is entered directly instead of reusing ``conftest``'s runner:
    the suite is collected from three roots with a ``conftest.py`` each, so a
    plain ``from conftest import ...`` resolves to whichever one is first on the
    path -- which is not this one.
    """
    updates: dict[str, object] = dict(overrides)
    if manifest is not None:
        updates["release_manifest_path"] = str(manifest)
    app = create_app(settings.model_copy(update=updates))

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


async def test_every_response_carries_the_published_version(settings, tmp_path) -> None:
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.2.0", "floor": "0.1.0"})

    async with serving(settings, manifest) as client:
        response = await client.get(
            "/v1/health",
            headers={VERSION_HEADER: "0.1.0", PLATFORM_HEADER: "android"},
        )

    assert response.status_code == 200
    assert response.headers[LATEST_HEADER] == "0.2.0"
    assert response.headers[MIN_HEADER] == "0.1.0"
    assert response.headers[ACTION_HEADER] == "optional"


async def test_an_app_below_the_floor_is_refused_with_426(settings, tmp_path) -> None:
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.3.0", "floor": "0.2.0"})

    async with serving(settings, manifest) as client:
        response = await client.get(
            "/v1/companies",
            headers={VERSION_HEADER: "0.1.0", PLATFORM_HEADER: "android"},
        )

    assert response.status_code == 426
    body = response.json()
    assert body["error"]["code"] == "update_required"
    assert body["error"]["retryable"] is False
    # The refusal carries what to install, so the app can start downloading from
    # the same response rather than fetching a manifest first.
    update = body["error"]["detail"]["update"]
    assert update["version"] == "0.3.0"
    assert update["url"] == "/downloads/TallyFlow-0.3.0.apk"
    assert update["sha256"] == "a" * 64


async def test_the_auth_routes_are_refused_too(settings, tmp_path) -> None:
    """An app too old to be served should not be handed a token either.

    Exempting auth would leave the one working path being the one that issues
    credentials to a client that cannot use them.
    """
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.3.0", "floor": "0.2.0"})

    async with serving(settings, manifest) as client:
        response = await client.post(
            "/v1/auth/register",
            json={"email": "a@b.c", "password": "Sufficiently-long-1"},
            headers={VERSION_HEADER: "0.1.0"},
        )

    assert response.status_code == 426


@pytest.mark.parametrize("path", ["/v1/health", "/v1/ready"])
async def test_the_probes_are_never_refused(settings, tmp_path, path: str) -> None:
    """A load balancer must not be told the *backend* is down over an app version."""
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.3.0", "floor": "0.2.0"})

    async with serving(settings, manifest) as client:
        response = await client.get(path, headers={VERSION_HEADER: "0.0.1"})

    assert response.status_code == 200


async def test_a_request_with_no_version_is_served(settings, tmp_path) -> None:
    """Web builds, curl, and every app build that predates the header."""
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.3.0", "floor": "0.2.0"})

    async with serving(settings, manifest) as client:
        response = await client.get("/v1/health")

    assert response.status_code == 200
    assert LATEST_HEADER not in response.headers


async def test_enforcement_can_be_switched_off_without_losing_the_advice(
    settings, tmp_path
) -> None:
    """The lever to pull when a bad floor has locked the fleet out.

    It has to restore service immediately, without waiting for a corrected
    manifest to reach every client -- so the headers keep flowing while the
    refusal stops.
    """
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.3.0", "floor": "0.2.0"})

    async with serving(settings, manifest, enforce_min_app_version=False) as client:
        response = await client.get("/v1/companies", headers={VERSION_HEADER: "0.1.0"})

    assert response.status_code != 426
    assert response.headers[ACTION_HEADER] == "required"


async def test_a_current_app_gets_no_advisory_headers(settings, tmp_path) -> None:
    """Nothing to say is said by saying nothing, not by an empty header."""
    manifest = write_manifest(tmp_path / "m.json", android={"version": "0.2.0", "floor": "0.1.0"})

    async with serving(settings, manifest) as client:
        response = await client.get("/v1/health", headers={VERSION_HEADER: "0.2.0"})

    assert response.status_code == 200
    assert LATEST_HEADER not in response.headers


async def test_a_backend_with_no_manifest_refuses_nobody(settings, tmp_path) -> None:
    """The deployment-mistake case: config missing, service unaffected."""
    async with serving(settings, tmp_path / "absent.json") as client:
        response = await client.get("/v1/health", headers={VERSION_HEADER: "0.0.1"})

    assert response.status_code == 200
    assert LATEST_HEADER not in response.headers
