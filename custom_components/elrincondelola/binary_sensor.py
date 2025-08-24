from __future__ import annotations

import logging
from typing import Optional

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, EVENT_SSE_UPDATE

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OcupadoBinarySensor(data["host"], data["token"])])


class OcupadoBinarySensor(BinarySensorEntity):
    _attr_should_poll = False

    def __init__(self, host: str, token: str) -> None:
        self._host = host
        self._token = token
        self._attr_name = "Ocupado"
        self._attr_unique_id = "elrincondelola_ocupado"
        self._is_on: bool = False
        self._attrs: dict = {}
        self._unsub = None

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict:
        return self._attrs

    async def async_added_to_hass(self) -> None:
        self._unsub = async_dispatcher_connect(self.hass, EVENT_SSE_UPDATE, self._handle_sse_update)
        await self._refresh_from_api()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _handle_sse_update(self) -> None:
        await self._refresh_from_api()

    async def _refresh_from_api(self) -> None:
        url = f"{self._host}/api/today"
        headers = {"Authorization": f"Bearer {self._token}"}
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    LOGGER.debug("/api/today responded %s", resp.status)
                    return
                data = await resp.json()
        except Exception as ex:
            LOGGER.debug("Error fetching /api/today: %s", ex)
            return

        has_res = bool(data.get("has_reservation", False))
        self._is_on = has_res
        self._attrs = {
            "reserva_hoy": has_res,
            "nombre": data.get("user_name"),
            "cumpleanos": bool(data.get("is_birthday", False)),
            "festivo": bool(data.get("is_holiday", False)),
            "foto_perfil_url": data.get("profile_pic_url"),
        }
        self.async_write_ha_state()

