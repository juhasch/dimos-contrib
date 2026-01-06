# Copyright 2025
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
from typing import Any

import requests

from dimos.core.core import rpc
from dimos.core.skill_module import SkillModule
from dimos.protocol.skill.skill import skill
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class HomeAssistantSkill(SkillModule):
    """Skill for controlling Home Assistant devices, starting with lights."""

    def __init__(self) -> None:
        super().__init__()
        self._base_url: str = os.getenv("HOME_ASSISTANT_URL", "https://tower.mmwaves.de:8123/")
        self._api_token: str = os.getenv("HOME_ASSISTANT_TOKEN", "")
        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }

    @rpc
    def start(self) -> None:
        super().start()
        if not self._api_token:
            logger.warning("HOME_ASSISTANT_TOKEN not set. Home Assistant skill may not work.")

    @rpc
    def stop(self) -> None:
        super().stop()

    def _make_request(
        self, method: str, endpoint: str, json_data: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any] | str:
        """Make a request to the Home Assistant API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., '/api/services/light/turn_on')
            json_data: Optional JSON data for POST requests

        Returns:
            Response data as dict or list on success, or error message as string
        """
        url = f"{self._base_url.rstrip('/')}{endpoint}"
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._headers,
                json=json_data,
                timeout=10,
            )
            response.raise_for_status()
            if response.content:
                return response.json()
            # Empty response also indicates success for service calls
            return []
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP error: {e.response.status_code} - {e.response.reason}"
            if e.response.content:
                try:
                    error_data = e.response.json()
                    error_msg += f" - {error_data}"
                except Exception:
                    error_msg += f" - {e.response.text}"
            logger.error(f"Home Assistant API error: {error_msg}")
            return error_msg
        except requests.exceptions.RequestException as e:
            error_msg = f"Request error: {str(e)}"
            logger.error(f"Home Assistant request error: {error_msg}")
            return error_msg
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.exception(f"Home Assistant unexpected error: {error_msg}")
            return error_msg

    @skill()
    def turn_on_light(self, entity_id: str) -> str:
        """Turn on a light in Home Assistant.

        Args:
            entity_id: The entity ID of the light (e.g., 'light.living_room', 'light.bedroom')

        Returns:
            str: Success or error message

        Example usage:
            turn_on_light("light.living_room")
            turn_on_light("light.bedroom")
        """
        result = self._make_request(
            "POST",
            "/api/services/light/turn_on",
            {"entity_id": entity_id},
        )
        # Home Assistant service calls return a list on success, or empty list for empty response
        if isinstance(result, (dict, list)):
            return f"Successfully turned on {entity_id}"
        return f"Failed to turn on {entity_id}: {result}"

    @skill()
    def turn_off_light(self, entity_id: str) -> str:
        """Turn off a light in Home Assistant.

        Args:
            entity_id: The entity ID of the light (e.g., 'light.living_room', 'light.bedroom')

        Returns:
            str: Success or error message

        Example usage:
            turn_off_light("light.living_room")
            turn_off_light("light.bedroom")
        """
        result = self._make_request(
            "POST",
            "/api/services/light/turn_off",
            {"entity_id": entity_id},
        )
        # Home Assistant service calls return a list on success, or empty list for empty response
        if isinstance(result, (dict, list)):
            return f"Successfully turned off {entity_id}"
        return f"Failed to turn off {entity_id}: {result}"

    @skill()
    def list_lights(self) -> str:
        """List all available lights in Home Assistant.

        Returns:
            str: A formatted list of available lights with their states, or an error message
        """
        result = self._make_request("GET", "/api/states")
        if isinstance(result, list):
            lights = [state for state in result if state.get("entity_id", "").startswith("light.")]
            if not lights:
                return "No lights found in Home Assistant"
            light_list = []
            for light in lights:
                entity_id = light.get("entity_id", "unknown")
                state = light.get("state", "unknown")
                friendly_name = light.get("attributes", {}).get("friendly_name", entity_id)
                light_list.append(f"- {entity_id} ({friendly_name}): {state}")
            return "Available lights:\n" + "\n".join(light_list)
        return f"Failed to list lights: {result}"

    @skill()
    def get_light_state(self, entity_id: str) -> str:
        """Get the current state of a light in Home Assistant.

        Args:
            entity_id: The entity ID of the light (e.g., 'light.living_room')

        Returns:
            str: The current state of the light, or an error message
        """
        result = self._make_request("GET", f"/api/states/{entity_id}")
        if isinstance(result, dict):
            state = result.get("state", "unknown")
            friendly_name = result.get("attributes", {}).get("friendly_name", entity_id)
            brightness = result.get("attributes", {}).get("brightness")
            brightness_pct = None
            if brightness is not None:
                brightness_pct = int((brightness / 255) * 100)
            state_str = f"{friendly_name} ({entity_id}) is {state}"
            if brightness_pct is not None:
                state_str += f" at {brightness_pct}% brightness"
            return state_str
        return f"Failed to get state for {entity_id}: {result}"


home_assistant_skill = HomeAssistantSkill.blueprint

__all__ = ["HomeAssistantSkill", "home_assistant_skill"]

