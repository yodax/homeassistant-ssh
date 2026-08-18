from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from functools import wraps
import logging

from ssh_terminal_manager import (
    ActionKey,
    Command,
    CommandOutput,
    SensorKey,
    SSHManager,
    SSHTerminal,
)
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_FLOOR_ID,
    ATTR_LABEL_ID,
    CONF_COMMAND,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
    CONF_VARIABLES,
    ENTITY_MATCH_ALL,
    Platform,
)
from homeassistant.core import (
    HomeAssistant,
    HomeAssistantError,
    ServiceCall,
    ServiceResponse,
    ServiceValidationError,
    SupportsResponse,
)
from homeassistant.helpers import (
    device_registry as dr,
    entity_platform,
    target as target_helpers,
)
from homeassistant.helpers.service import async_extract_config_entry_ids

from .base_entity import BaseSensorEntity
from .const import (
    CONF_ALLOW_TURN_OFF,
    CONF_COMMAND_TIMEOUT,
    CONF_DISCONNECT_MODE,
    CONF_DYNAMIC,
    CONF_HOST_KEYS_FILENAME,
    CONF_INVOKE_SHELL,
    CONF_KEY,
    CONF_KEY_FILENAME,
    CONF_LOAD_SYSTEM_HOST_KEYS,
    CONF_POWER_BUTTON,
    CONF_SENSOR_COMMANDS,
    CONF_SENSORS,
    CONF_SEPARATOR,
    CONF_UPDATE_INTERVAL,
    CONF_VALUES,
    DOMAIN,
    SERVICE_EXECUTE_COMMAND,
    SERVICE_POLL_SENSOR,
    SERVICE_RESTART,
    SERVICE_RUN_ACTION,
    SERVICE_SET_VALUE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from .converter import Converter
from .coordinator import (
    SensorCommandCoordinator,
    StateCoordinator,
    get_failed_exit_code,
)
from .entry_data import EntryData
from .helpers import (
    get_command_renderer,
    get_device_info,
    get_device_sensor_update_handler,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.UPDATE,
]

DEVICE_SENSOR_KEYS = [
    SensorKey.MACHINE_TYPE,
    SensorKey.OS_NAME,
    SensorKey.OS_VERSION,
    SensorKey.OS_RELEASE,
    SensorKey.DEVICE_NAME,
    SensorKey.DEVICE_MODEL,
    SensorKey.MANUFACTURER,
    SensorKey.CPU_NAME,
    SensorKey.CPU_CORES,
    SensorKey.CPU_HARDWARE,
    SensorKey.CPU_MODEL,
    SensorKey.TOTAL_MEMORY,
]

# services.yaml declares a full target selector, so the frontend can send
# area_id / floor_id / label_id as well as device_id / entity_id. A plain
# vol.Schema defaults to PREVENT_EXTRA and rejected those outright, which made
# picking an area in the service dialog fail with "extra keys not allowed".
TARGET_FIELDS = {
    vol.Optional(ATTR_AREA_ID): vol.Any(str, list),
    vol.Optional(ATTR_DEVICE_ID): vol.Any(str, list),
    vol.Optional(ATTR_ENTITY_ID): vol.Any(str, list),
    vol.Optional(ATTR_FLOOR_ID): vol.Any(str, list),
    vol.Optional(ATTR_LABEL_ID): vol.Any(str, list),
}

# Stock sensors whose value cannot change while the host is up, so a sensor
# command without a scan interval is correct for them and must not be warned
# about. Everything else in SensorKey (cpu_load, free_memory, free_disk_space,
# processes, temperature) can change, as can any user-defined sensor.
STATIC_SENSOR_KEYS = frozenset(
    {
        SensorKey.CPU_CORES,
        SensorKey.CPU_HARDWARE,
        SensorKey.CPU_MODEL,
        SensorKey.CPU_NAME,
        SensorKey.DEVICE_MODEL,
        SensorKey.DEVICE_NAME,
        SensorKey.HOSTNAME,
        SensorKey.MACHINE_TYPE,
        SensorKey.MAC_ADDRESS,
        SensorKey.MANUFACTURER,
        SensorKey.NETWORK_INTERFACE,
        SensorKey.OS_ARCHITECTURE,
        SensorKey.OS_NAME,
        SensorKey.OS_RELEASE,
        SensorKey.OS_VERSION,
        SensorKey.SERIAL_NUMBER,
        SensorKey.TOTAL_MEMORY,
        SensorKey.WAKE_ON_LAN,
    }
)

EXECUTE_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COMMAND): str,
        vol.Optional(CONF_TIMEOUT): int,
        vol.Optional(CONF_VARIABLES): dict,
        **TARGET_FIELDS,
    }
)

RUN_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_KEY): str,
        vol.Optional(CONF_VARIABLES): dict,
        **TARGET_FIELDS,
    }
)

SET_VALUE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_VALUES): list,
        vol.Required(ATTR_ENTITY_ID): vol.Any(str, list),
        **{key: value for key, value in TARGET_FIELDS.items() if key != ATTR_ENTITY_ID},
    }
)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Migrate old entry."""
    _LOGGER.debug(
        "Migrating configuration from version %s.%s",
        entry.version,
        entry.minor_version,
    )

    if entry.version > 2:
        return False

    if entry.version == 1:
        new_data = {**entry.data}
        new_options = {**entry.options}

        if entry.minor_version < 2:
            new_data[CONF_LOAD_SYSTEM_HOST_KEYS] = True
            new_options[CONF_DISCONNECT_MODE] = False

        if entry.minor_version < 3:
            new_data[CONF_INVOKE_SHELL] = False

        if entry.minor_version < 4:
            for command_config in new_options[CONF_SENSOR_COMMANDS]:
                for sensor_config in reversed(command_config[CONF_SENSORS]):
                    if not (separator := sensor_config.get(CONF_SEPARATOR)):
                        continue
                    sensor_config.pop(CONF_SEPARATOR)
                    if sensor_config.get(CONF_DYNAMIC):
                        command_config[CONF_SEPARATOR] = separator

        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options, minor_version=1, version=2
        )

    if entry.version == 2:
        new_data = {**entry.data}
        new_options = {**entry.options}

        if entry.minor_version < 2:
            new_options[CONF_POWER_BUTTON] = True

        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options, minor_version=2, version=2
        )

    _LOGGER.debug(
        "Migration to configuration version %s.%s successful",
        entry.version,
        entry.minor_version,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SSH from a config entry."""
    data = entry.data
    options = entry.options

    terminal = SSHTerminal(
        data[CONF_HOST],
        port=data[CONF_PORT],
        username=data.get(CONF_USERNAME),
        password=data.get(CONF_PASSWORD),
        key_filename=data.get(CONF_KEY_FILENAME),
        host_keys_filename=data.get(CONF_HOST_KEYS_FILENAME),
        load_system_host_keys=data[CONF_LOAD_SYSTEM_HOST_KEYS],
        invoke_shell=data[CONF_INVOKE_SHELL],
    )

    manager = SSHManager(
        terminal,
        name=data[CONF_NAME],
        command_timeout=options[CONF_COMMAND_TIMEOUT],
        allow_turn_off=options[CONF_ALLOW_TURN_OFF],
        disconnect_mode=options[CONF_DISCONNECT_MODE],
        mac_address=data[CONF_MAC],
        collection=Converter(hass).get_collection(options),
        logger=_LOGGER,
    )

    await manager.async_load_host_keys()

    await async_initialize_entry(
        hass,
        entry,
        manager,
        PLATFORMS,
        ignored_action_keys=[ActionKey.TURN_OFF],
    )

    async_register_services(hass, DOMAIN)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Setup can fail before hass.data is populated - loading host keys is the
    # usual culprit - and HA still unloads the entry afterwards. Indexing
    # directly raised a KeyError that masked the original setup error.
    entry_data: EntryData | None = hass.data.get(entry.domain, {}).get(entry.entry_id)

    if entry_data is None:
        return True

    platforms = entry_data.platforms

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, platforms):
        hass.data[entry.domain].pop(entry.entry_id, None)
        await entry_data.async_shutdown()

    return unload_ok


async def async_initialize_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    manager: SSHManager,
    platforms: list[Platform],
    ignored_action_keys: list[ActionKey] | None = None,
    ignored_sensor_keys: list[SensorKey] | None = None,
):
    """Initialize a config entry."""
    state_coordinator = StateCoordinator(
        hass, manager, entry.options[CONF_UPDATE_INTERVAL]
    )

    command_coordinators = [
        SensorCommandCoordinator(hass, manager, command)
        for command in manager.sensor_commands
    ]

    async_warn_about_never_refreshing_commands(manager)

    entry_data = EntryData(
        entry,
        manager,
        state_coordinator,
        command_coordinators,
        platforms,
        ignored_action_keys,
        ignored_sensor_keys,
    )

    # No update listener here on purpose: the options flow subclasses
    # OptionsFlowWithReload, which reloads the entry itself. Registering both
    # reloaded twice per change and is refused by HA from 2026.12.
    hass.data.setdefault(entry.domain, {})
    hass.data[entry.domain][entry.entry_id] = entry_data

    await state_coordinator.async_config_entry_first_refresh()

    device_registry = dr.async_get(hass)
    entry_data.device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(entry.domain, entry.unique_id)},
        name=manager.name,
        **get_device_info(manager),
    )

    handle_device_sensor_update = get_device_sensor_update_handler(
        hass, entry_data, device_registry
    )

    for key in DEVICE_SENSOR_KEYS:
        if sensor := manager.sensors_by_key.get(key):
            sensor.on_update.subscribe(handle_device_sensor_update)

    await hass.config_entries.async_forward_entry_setups(entry, platforms)


def async_warn_about_never_refreshing_commands(manager: SSHManager) -> None:
    """Log sensor commands that will only ever run once.

    A sensor command with no scan_interval gets a coordinator with
    update_interval=None, and HA never arms a refresh timer for it. The
    manager's own periodic update skips any command that has already produced
    output, so the command runs once per entry load and then never again while
    its entities stay available and keep showing the startup value.

    That is the right behaviour for static facts (uname, dmidecode, cpuinfo),
    and a silently dead signal for anything that can change - a pool health or
    backup status check reads healthy forever. Neither the UI nor the log said
    so, which is the part worth fixing here.
    """
    for command in manager.sensor_commands:
        if command.interval:
            continue
        keys = [sensor.key for sensor in command.sensors]
        if all(key in STATIC_SENSOR_KEYS for key in keys):
            continue
        _LOGGER.warning(
            "%s: sensor command for %s has no scan interval, so it runs once at "
            "startup and never refreshes. Set a scan interval, or poll it with "
            "the ssh.poll_sensor action, if its value can change",
            manager.name,
            ", ".join(keys),
        )


def get_targeted_entities(
    hass: HomeAssistant,
    entities: list[BaseSensorEntity],
    call: ServiceCall,
) -> tuple[list[BaseSensorEntity], list[BaseSensorEntity]]:
    """Split the entities a call targets into available and unavailable.

    async_extract_entities drops unavailable entities silently. Every entity of
    an entry is unavailable while its host is unreachable, so a poll against a
    host that is down resolved to an empty selection and returned an empty
    result set - indistinguishable from a successful poll. The caller has to be
    able to tell those apart, so return the dropped ones rather than discarding
    them.
    """
    if call.data.get(ATTR_ENTITY_ID) == ENTITY_MATCH_ALL:
        matched = list(entities)
    else:
        referenced = target_helpers.async_extract_referenced_entity_ids(
            hass, target_helpers.TargetSelection(call.data), True
        )
        combined = referenced.referenced | referenced.indirectly_referenced
        matched = [entity for entity in entities if entity.entity_id in combined]

    return (
        [entity for entity in matched if entity.available],
        [entity for entity in matched if not entity.available],
    )


def get_poll_result(
    entry_data: EntryData, entity: BaseSensorEntity, error: Exception | None
) -> dict:
    """Build the result row for one polled sensor.

    A sensor command that runs but exits non-zero is reported by
    terminal_manager as a success with no error, while it clears every sensor
    value that command feeds. Reporting that as a successful poll is how a
    health check can go to `unknown` and still look fine to whoever polled it.
    """
    result = {
        "entity_id": entity.entity_id,
        "entity_name": entity.name,
        "success": error is None,
    }

    if error is not None:
        result["error"] = str(error)
        return result

    try:
        command = entry_data.manager.get_sensor_command(entity.key)
    except KeyError:
        return result

    if (code := get_failed_exit_code(command)) is not None:
        result["success"] = False
        result["code"] = code
        result["error"] = (
            "; ".join(command.output.stderr)
            or f"sensor command exited with code {code}; the sensor value was cleared"
        )

    return result


def get_unavailable_results(entities: list[BaseSensorEntity]) -> list[dict]:
    """Build failure rows for targeted entities that could not be reached."""
    return [
        {
            "entity_id": entity.entity_id,
            "entity_name": entity.name,
            "success": False,
            "error": "entity is unavailable, the host may be unreachable",
        }
        for entity in entities
    ]


def get_targeted_entry_data(
    hass: HomeAssistant, domain: str, entry_ids: set[str]
) -> list[EntryData]:
    """Return the loaded entry data for the targeted config entries.

    A device can carry config entries belonging to other integrations - template
    helpers assigned to the SSH device for organisation are the common case - and
    an SSH entry can exist but not be loaded. Indexing hass.data blindly raised a
    bare KeyError in both situations.
    """
    domain_data = hass.data.get(domain, {})

    if entry_data := [
        domain_data[entry_id] for entry_id in entry_ids if entry_id in domain_data
    ]:
        return entry_data

    if any(
        (entry := hass.config_entries.async_get_entry(entry_id))
        and entry.domain == domain
        for entry_id in entry_ids
    ):
        raise ServiceValidationError(
            f"The targeted {domain} config entry is not loaded"
        )

    raise ServiceValidationError(
        f"No loaded {domain} config entry found for the specified target"
    )


def async_register_services(hass: HomeAssistant, domain: str):
    """Register the domain services."""

    def get_response(coro: Coroutine):
        @wraps(coro)
        async def wrapper(call: ServiceCall) -> ServiceResponse | None:
            entry_ids = await async_extract_config_entry_ids(call)
            data = await asyncio.gather(
                *(
                    coro(entry_data, call)
                    for entry_data in get_targeted_entry_data(hass, domain, entry_ids)
                )
            )
            results = [result for results in data for result in results]

            # Without a response_variable the caller never sees these results,
            # so a failure would otherwise be discarded in silence: the calling
            # script carries on as if the remote command had run. Log every
            # failure, and raise when nobody is going to read the response.
            if failures := [result for result in results if not result["success"]]:
                for failure in failures:
                    _LOGGER.error(
                        "%s failed on %s: %s",
                        call.service,
                        failure.get("device_name") or failure.get("entity_name"),
                        failure.get("error", "unknown error"),
                    )
                if not call.return_response:
                    raise HomeAssistantError(
                        f"{call.service} failed: "
                        + "; ".join(
                            failure.get("error", "unknown error")
                            for failure in failures
                        )
                    )

            return {"results": results} if call.return_response else None

        return wrapper

    def get_command_result(coro: Coroutine):
        @wraps(coro)
        async def wrapper(entry_data: EntryData, call: ServiceCall) -> list[dict]:
            try:
                output: CommandOutput = await coro(entry_data, call)
            except Exception as exc:  # noqa: BLE001
                result = {
                    "device_id": entry_data.device_entry.id,
                    "device_name": entry_data.device_entry.name,
                    "success": False,
                    "error": str(exc),
                }
            else:
                # A command that ran but exited non-zero is a failed command.
                # Upstream reported success for any exit code, so an action
                # button wired to a script that errors out looked like it had
                # worked.
                result = {
                    "device_id": entry_data.device_entry.id,
                    "device_name": entry_data.device_entry.name,
                    "success": output.code == 0,
                    "command": output.command_string,
                    "stdout": output.stdout,
                    "stderr": output.stderr,
                    "code": output.code,
                }
                if output.code != 0:
                    result["error"] = (
                        "\n".join(output.stderr)
                        or f"command exited with code {output.code}"
                    )
            return [result]

        return wrapper

    def get_generic_result(coro: Coroutine):
        @wraps(coro)
        async def wrapper(entry_data: EntryData, call: ServiceCall) -> list[dict]:
            try:
                await coro(entry_data, call)
            except Exception as exc:  # noqa: BLE001
                result = {
                    "device_id": entry_data.device_entry.id,
                    "device_name": entry_data.device_entry.name,
                    "success": False,
                    "error": str(exc),
                }
            else:
                result = {
                    "device_id": entry_data.device_entry.id,
                    "device_name": entry_data.device_entry.name,
                    "success": True,
                }
            return [result]

        return wrapper

    @get_response
    @get_command_result
    async def execute_command(
        entry_data: EntryData, call: ServiceCall
    ) -> CommandOutput:
        command = Command(
            call.data[CONF_COMMAND],
            timeout=call.data.get(CONF_TIMEOUT),
            renderer=get_command_renderer(hass),
        )
        variables = call.data.get(CONF_VARIABLES)
        return await entry_data.manager.async_execute_command(command, variables)

    @get_response
    @get_command_result
    async def run_action(entry_data: EntryData, call: ServiceCall) -> CommandOutput:
        action_key = call.data[CONF_KEY]
        variables = call.data.get(CONF_VARIABLES)
        return await entry_data.manager.async_run_action(action_key, variables)

    @get_response
    async def poll_sensor(entry_data: EntryData, call: ServiceCall) -> list[dict]:
        entities = [
            entity
            for platform in entity_platform.async_get_platforms(hass, domain)
            for entity in platform.entities.values()
            if isinstance(entity, BaseSensorEntity)
            and entity.coordinator == entry_data.state_coordinator
        ]
        selected_entities, unavailable_entities = get_targeted_entities(
            hass, entities, call
        )
        sensor_keys = [entity.key for entity in selected_entities]
        sensors, errors = await entry_data.manager.async_poll_sensors(
            sensor_keys,
            raise_errors=False,
        )
        return [
            get_poll_result(entry_data, entity, errors[i])
            for i, entity in enumerate(selected_entities)
        ] + get_unavailable_results(unavailable_entities)

    @get_response
    async def set_value(entry_data: EntryData, call: ServiceCall) -> list[dict]:
        values = call.data[CONF_VALUES]
        entities = [
            entity
            for platform in entity_platform.async_get_platforms(hass, domain)
            for entity in platform.entities.values()
            if isinstance(entity, BaseSensorEntity)
            and entity.coordinator == entry_data.state_coordinator
        ]
        selected_entities, unavailable_entities = get_targeted_entities(
            hass, entities, call
        )
        # Values are paired with entities by index. Dropping an unavailable
        # entity from the middle of the selection shifted every later value onto
        # the wrong sensor, silently, and the old length guard only caught the
        # case where there were too few values.
        if unavailable_entities:
            raise ServiceValidationError(
                "Cannot set values while these entities are unavailable: "
                + ", ".join(entity.entity_id for entity in unavailable_entities)
            )
        if len(selected_entities) != len(values):
            raise ServiceValidationError(
                f"Got {len(values)} values for {len(selected_entities)} entities; "
                "provide exactly one value per targeted entity"
            )
        sensor_keys = [entity.key for entity in selected_entities]
        sensors, errors = await entry_data.manager.async_set_sensor_values(
            sensor_keys,
            values,
            raise_errors=False,
        )
        return [
            {
                "entity_id": entity.entity_id,
                "entity_name": entity.name,
                "success": (error := errors[i]) is None,
                **({"error": str(error)} if error else {}),
            }
            for i, entity in enumerate(selected_entities)
        ]

    @get_response
    @get_generic_result
    async def turn_on(entry_data: EntryData, call: ServiceCall) -> None:
        await entry_data.state_coordinator.async_turn_on()

    @get_response
    @get_command_result
    async def turn_off(entry_data: EntryData, call: ServiceCall) -> CommandOutput:
        return await entry_data.state_coordinator.async_turn_off()

    @get_response
    @get_command_result
    async def restart(entry_data: EntryData, call: ServiceCall) -> CommandOutput:
        return await entry_data.state_coordinator.async_restart()

    hass.services.async_register(
        domain,
        SERVICE_EXECUTE_COMMAND,
        execute_command,
        EXECUTE_COMMAND_SCHEMA,
        SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        domain,
        SERVICE_RUN_ACTION,
        run_action,
        RUN_ACTION_SCHEMA,
        SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        domain,
        SERVICE_POLL_SENSOR,
        poll_sensor,
        None,
        SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        domain,
        SERVICE_SET_VALUE,
        set_value,
        SET_VALUE_SCHEMA,
        SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        domain,
        SERVICE_TURN_ON,
        turn_on,
        None,
        SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        domain,
        SERVICE_TURN_OFF,
        turn_off,
        None,
        SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        domain,
        SERVICE_RESTART,
        restart,
        None,
        SupportsResponse.OPTIONAL,
    )
