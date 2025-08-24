from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Optional

import aiohttp
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change

from homeassistant.helpers.dispatcher import async_dispatcher_send, async_dispatcher_connect

from .const import DOMAIN, EVENT_SSE_UPDATE

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    host = data["host"]
    token = data["token"]
    entities = [ApiPingSensor(host, token), ReservaHoySensor(host, token), ReservaAnteriorSensor(host, token), ReservaProximaSensor(host, token)]
    async_add_entities(entities)


class ApiPingSensor(SensorEntity):
    """Sensor that reflects last event from SSE stream."""

    def __init__(self, host: str, token: str) -> None:
        self._host = host
        self._token = token
        self._attr_name = "El Rincón de Lola API"
        self._attr_unique_id = "elrincondelola_api"
        self._attr_native_value: Optional[str] = None
        self._attr_should_poll = False
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def async_added_to_hass(self) -> None:
        LOGGER.debug("Starting SSE listener for %s", self._attr_unique_id)
        self._running = True
        self._task = self.hass.async_create_task(self._listen_sse())

    async def async_will_remove_from_hass(self) -> None:
        LOGGER.debug("Stopping SSE listener for %s", self._attr_unique_id)
        self._running = False
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _listen_sse(self) -> None:
        """Listen to the API SSE endpoint and update state on events.

        Implements backoff reconnection and ignores keep-alive pings.
        """
        session = async_get_clientsession(self.hass)
        url = f"{self._host}/api/events"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "text/event-stream",
        }
        backoff = 10
        while self._running:
            try:
                LOGGER.debug("Connecting to SSE %s", url)
                async with session.get(url, headers=headers) as resp:
                    LOGGER.debug("SSE response status: %s", resp.status)
                    if resp.status != 200:
                        if resp.status == 401:
                            LOGGER.warning(
                                "SSE auth failed (401). Token may be invalid/expired. Reconfigure the integration."
                            )
                        # Incrementar backoff con jitter para reducir carga en errores
                        delay = backoff * (0.8 + 0.4 * (asyncio.get_running_loop().time() % 1))
                        await asyncio.sleep(delay)
                        backoff = min(backoff * 2, 300)
                        continue
                    # Conectado correctamente; seguir esperando eventos
                    backoff = 5

                    buffer = ""
                    async for raw in resp.content:
                        if not self._running:
                            break
                        try:
                            line = raw.decode("utf-8").rstrip("\n")
                        except Exception:
                            continue

                        if not line:
                            if buffer:
                                LOGGER.debug("SSE event assembled: %s", buffer)
                                self._attr_native_value = buffer
                                buffer = ""
                                self.async_write_ha_state()
                                # Notificar a otras entidades de la integración
                                async_dispatcher_send(self.hass, EVENT_SSE_UPDATE)
                            continue
                        if line.startswith(":"):
                            # Comment/keep-alive (e.g., ": ping")
                            continue
                        if line.startswith("data:"):
                            buffer += line[5:].lstrip()
                            continue
                        # Ignore other fields (event:, id:)

            except asyncio.CancelledError:
                raise
            except Exception as ex:
                LOGGER.debug("SSE error: %s", ex)
                delay = backoff * (0.8 + 0.4 * (asyncio.get_running_loop().time() % 1))
                await asyncio.sleep(delay)
                backoff = min(backoff * 2, 300)


class ReservaHoySensor(SensorEntity):
    """Sensor que indica si hay reserva hoy y sus atributos."""

    _attr_should_poll = False

    def __init__(self, host: str, token: str) -> None:
        self._host = host
        self._token = token
        self._attr_name = "Reserva Hoy"
        self._attr_unique_id = "elrincondelola_reserva_hoy"
        self._attr_native_value: Optional[str] = None
        self._attrs: dict = {}
        self._unsubs = []

    @property
    def extra_state_attributes(self) -> dict:
        return self._attrs

    async def async_added_to_hass(self) -> None:
        # Suscribirse a eventos SSE de la misma integración para refrescar
        self._unsubs.append(
            async_dispatcher_connect(self.hass, EVENT_SSE_UPDATE, self._handle_sse_update)
        )
        # También refrescar automáticamente al cambio de día local (00:00)
        self._unsubs.append(
            async_track_time_change(self.hass, self._handle_midnight_tick, hour=0, minute=0, second=0)
        )
        # Inicial: cargar estado de la API
        await self._refresh_from_api()
        # Sin polling nocturno: refrescamos sólo por eventos SSE

    async def async_will_remove_from_hass(self) -> None:
        # Cancelar todas las suscripciones
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs = []

    async def _handle_sse_update(self) -> None:
        await self._refresh_from_api()

    async def _handle_midnight_tick(self, now=None) -> None:
        # Forzar refresco al cambiar el día
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

        has_res = data.get("has_reservation")
        if has_res:
            name = data.get("user_name") or "Desconocido"
            self._attr_native_value = name
        else:
            self._attr_native_value = "Libre"
        self._attrs = {
            "cumpleanos": bool(data.get("is_birthday", False)),
            "festivo": bool(data.get("is_holiday", False)),
            "foto_perfil_url": data.get("profile_pic_url"),
        }
        self.async_write_ha_state()


class _ReservaBase(SensorEntity):
    _attr_should_poll = False

    def __init__(self, host: str, token: str, name: str, unique_id: str, endpoint: str) -> None:
        self._host = host
        self._token = token
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_value: Optional[str] = None
        self._attrs: dict = {}
        self._endpoint = endpoint
        self._unsubs = []

    @property
    def extra_state_attributes(self) -> dict:
        return self._attrs

    async def async_added_to_hass(self) -> None:
        self._unsubs.append(
            async_dispatcher_connect(self.hass, EVENT_SSE_UPDATE, self._handle_sse_update)
        )
        # Refrescar al cambio de día local (00:00)
        self._unsubs.append(
            async_track_time_change(self.hass, self._handle_midnight_tick, hour=0, minute=0, second=0)
        )
        await self._refresh_from_api()

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs = []

    async def _handle_sse_update(self) -> None:
        await self._refresh_from_api()

    async def _handle_midnight_tick(self, now=None) -> None:
        # Forzar refresco al cambiar el día
        await self._refresh_from_api()

    async def _refresh_from_api(self) -> None:
        url = f"{self._host}{self._endpoint}"
        headers = {"Authorization": f"Bearer {self._token}"}
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    LOGGER.debug("%s responded %s", self._endpoint, resp.status)
                    return
                data = await resp.json()
        except Exception as ex:
            LOGGER.debug("Error fetching %s: %s", self._endpoint, ex)
            return

        if data.get("has_reservation"):
            # Estado = nombre; fecha va como atributo
            self._attr_native_value = data.get("user_name") or data.get("date") or "Desconocido"
        else:
            self._attr_native_value = "Ninguna"
        self._attrs = {
            "nombre": data.get("user_name"),
            "cumpleanos": bool(data.get("is_birthday", False)),
            "festivo": bool(data.get("is_holiday", False)),
            "foto_perfil_url": data.get("profile_pic_url"),
            "fecha": data.get("date"),
        }
        self.async_write_ha_state()


class ReservaAnteriorSensor(_ReservaBase):
    def __init__(self, host: str, token: str) -> None:
        super().__init__(host, token, "Anterior Reserva", "elrincondelola_reserva_anterior", "/api/prev")


class ReservaProximaSensor(_ReservaBase):
    def __init__(self, host: str, token: str) -> None:
        super().__init__(host, token, "Próxima Reserva", "elrincondelola_reserva_proxima", "/api/next")

    # Keep async_update for compatibility; no-op since push-based
    async def async_update(self) -> None:  # noqa: D401
        return
