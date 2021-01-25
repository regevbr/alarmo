import json
import logging

from homeassistant.core import (
    HomeAssistant,
    callback,
)

from homeassistant.components.mqtt import (
    DOMAIN as ATTR_MQTT,
    CONF_STATE_TOPIC,
    CONF_COMMAND_TOPIC,
)

import homeassistant.components.mqtt as mqtt

from homeassistant.util import slugify
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from . import const

_LOGGER = logging.getLogger(__name__)


class MqttHandler:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._config = None
        self._subscribed_topics = []

        async def async_update_config(_args=None):
            """mqtt config updated, reload the configuration."""
            old_config = self._config
            new_config = self.hass.data[const.DOMAIN]["coordinator"].store.async_get_config()

            if old_config and old_config[ATTR_MQTT] == new_config[ATTR_MQTT]:
                # only update MQTT config if some parameters are changed
                return

            self._config = new_config

            if not old_config or old_config[ATTR_MQTT][CONF_COMMAND_TOPIC] != new_config[ATTR_MQTT][CONF_COMMAND_TOPIC]:
                # re-subscribing is only needed if the command topic has changed
                await self._async_subscribe_topics()

            _LOGGER.debug("MQTT config was (re)loaded")

        async_dispatcher_connect(hass, "alarmo_config_updated", async_update_config)
        self.hass.async_add_job(async_update_config)

        @callback
        def async_alarm_state_changed(area_id: str, old_state: str, new_state: str):

            if not self._config[ATTR_MQTT][const.ATTR_ENABLED]:
                return

            topic = self._config[ATTR_MQTT][CONF_STATE_TOPIC]
            if area_id and len(self.hass.data[const.DOMAIN]["areas"]) > 1:
                # handle the sending of a state update for a specific area
                area = self.hass.data[const.DOMAIN]["areas"][area_id]
                topic = topic.rsplit('/', 1)
                topic.insert(1, slugify(area.name))
                topic = "/".join(topic)

            payload_config = self._config[ATTR_MQTT][const.ATTR_STATE_PAYLOAD]
            if new_state in payload_config and payload_config[new_state]:
                message = payload_config[new_state]
            else:
                message = new_state

            mqtt.async_publish(self.hass, topic, message, retain=True)
            _LOGGER.debug("Published state '{}' on topic '{}'".format(message, topic))

        async_dispatcher_connect(self.hass, "alarmo_state_updated", async_alarm_state_changed)

        # @callback
        # def async_failed_to_arm(area_id: str):
        #     _LOGGER.debug("area {} has failed to arm".format(area_id))

        # async_dispatcher_connect(self.hass, "alarmo_failed_to_arm", async_failed_to_arm)

    async def _async_subscribe_topics(self):
        """install a listener for the command topic."""

        self._unsubscribe_topics()
        if not self._config[ATTR_MQTT][const.ATTR_ENABLED]:
            return

        self._subscribed_topics.append(
                await mqtt.async_subscribe(
                    self.hass,
                    self._config[ATTR_MQTT][CONF_COMMAND_TOPIC],
                    self.async_message_received,
                )
        )

    def _unsubscribe_topics(self):
        """remove listeners."""
        while len(self._subscribed_topics):
            self._subscribed_topics.pop()()

    @callback
    async def async_message_received(self, msg):

        command = None
        code = None
        area = None
        try:
            payload = json.loads(msg.payload)
            payload = {k.lower(): v for k, v in payload.items()}

            if "command" in payload:
                command = payload["command"]
            elif "cmd" in payload:
                command = payload["cmd"]
            elif "action" in payload:
                command = payload["action"]
            elif "state" in payload:
                command = payload["state"]

            if "code" in payload:
                code = payload["code"]
            elif "pin" in payload:
                code = payload["pin"]
            elif "password" in payload:
                code = payload["password"]
            elif "pincode" in payload:
                code = payload["pincode"]

            if "area" in payload and payload["area"]:
                area = payload["area"]

        except ValueError:
            # no JSON structure found
            command = msg.payload
            code = None

        if type(command) is str:
            command = command.lower()
        else:
            _LOGGER.warning("Received unexpected command")
            return

        payload_config = self._config[ATTR_MQTT][const.ATTR_COMMAND_PAYLOAD]
        skip_code = not self._config[ATTR_MQTT][const.ATTR_REQUIRE_CODE]

        command_payloads = {}
        for item in const.COMMANDS:
            if item in payload_config and payload_config[item]:
                command_payloads[item] = payload_config[item].lower()
            elif item not in payload_config:
                command_payloads[item] = item.lower()

        if command not in list(command_payloads.values()):
            _LOGGER.warning("Received unexpected command: %s", command)
            return

        if area:
            res = list(filter(lambda el: slugify(el.name) == area, self.hass.data[const.DOMAIN]["areas"].values()))
            if not res:
                _LOGGER.warning("Area {} does not exist".format(area))
                return
            entity = res[0]
        else:
            if self._config[const.ATTR_MASTER][const.ATTR_ENABLED] and len(self.hass.data[const.DOMAIN]["areas"]) > 1:
                entity = self.hass.data[const.DOMAIN]["master"]
            elif len(self.hass.data[const.DOMAIN]["areas"]) == 1:
                entity = self.hass.data[const.DOMAIN]["areas"][0]
            else:
                _LOGGER.warning("No area specified")
                return

        _LOGGER.debug("Received command {}".format(command))

        if command == command_payloads[const.COMMAND_DISARM]:
            await entity.async_alarm_disarm(code, skip_code)
        elif command == command_payloads[const.COMMAND_ARM_AWAY]:
            await entity.async_alarm_arm_away(code, skip_code)
        elif command == command_payloads[const.COMMAND_ARM_NIGHT]:
            await entity.async_alarm_arm_night(code, skip_code)
        elif command == command_payloads[const.COMMAND_ARM_HOME]:
            await entity.async_alarm_arm_home(code, skip_code)
        elif command == command_payloads[const.COMMAND_ARM_CUSTOM_BYPASS]:
            await entity.async_alarm_arm_custom_bypass(code, skip_code)