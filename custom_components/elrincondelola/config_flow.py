from __future__ import annotations

import re
import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from typing import Any
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_HOST, CONF_USERNAME, CONF_PASSWORD

class ElRinconLolaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for El Rincón de Lola."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                token = await self._login_and_get_token(
                    user_input[CONF_HOST],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except Exception:
                errors["base"] = "auth"
            else:
                return self.async_create_entry(
                    title="El Rincón de Lola",
                    data={"host": user_input[CONF_HOST], "token": token},
                )
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def _login_and_get_token(self, host: str, username: str, password: str) -> str:
        login_url = f"{host}/login"
        token_url = f"{host}/oauth/token"
        async with aiohttp.ClientSession() as session:
            async with session.get(login_url) as resp:
                text = await resp.text()
            match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', text)
            if not match:
                raise RuntimeError("CSRF token not found")
            csrf = match.group(1)
            payload = {
                "csrf_token": csrf,
                "email_or_username": username,
                "password": password,
                "remember": "y",
            }
            async with session.post(login_url, data=payload) as resp:
                if resp.status not in (200, 302):
                    raise RuntimeError("Login failed")
            async with session.post(token_url) as resp:
                if resp.status != 200:
                    raise RuntimeError("Token request failed")
                data = await resp.json()
            return data["access_token"]


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors = {}
        data = self.config_entry.data
        options = self.config_entry.options
        if user_input is not None:
            # Intentar login para obtener token actualizado
            host = user_input[CONF_HOST]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            try:
                token = await ElRinconLolaConfigFlow._login_and_get_token(self, host, username, password)
            except Exception:
                errors["base"] = "auth"
            else:
                # Guardar en data (host/token) y vaciar options
                new_data = {
                    **data,
                    "host": host,
                    "token": token,
                }
                await self.hass.config_entries.async_update_entry(self.config_entry, data=new_data, options={})
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                return self.async_create_entry(title="", data={})

        # Valores por defecto desde data/options
        default_host = options.get("host", data.get("host", ""))
        default_username = options.get("username", "")
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=default_host): str,
                vol.Required(CONF_USERNAME, default=default_username): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=data_schema, errors=errors)


async def async_get_options_flow(config_entry: config_entries.ConfigEntry):
    return OptionsFlowHandler(config_entry)
