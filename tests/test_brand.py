"""Home Assistant serves the bundled brand images of the integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

BRAND_DIR = Path(__file__).parent.parent / "custom_components" / "eblocker" / "brand"


@pytest.mark.parametrize(
    "image",
    [
        "icon.png",
        "icon@2x.png",
        "logo.png",
        "logo@2x.png",
        "dark_logo.png",
        "dark_logo@2x.png",
    ],
)
async def test_brand_images_served(hass: HomeAssistant, hass_client, image: str):
    """The brands API returns our own PNGs instead of the CDN version."""
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()

    resp = await client.get(f"/api/brands/integration/eblocker/{image}")

    assert resp.status == 200
    assert resp.content_type == "image/png"
    assert await resp.read() == (BRAND_DIR / image).read_bytes()


async def test_dark_icon_falls_back_to_icon(hass: HomeAssistant, hass_client):
    """No dark icon is shipped; the orange icon also works on dark themes."""
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()

    resp = await client.get("/api/brands/integration/eblocker/dark_icon.png")

    assert resp.status == 200
    assert await resp.read() == (BRAND_DIR / "icon.png").read_bytes()
