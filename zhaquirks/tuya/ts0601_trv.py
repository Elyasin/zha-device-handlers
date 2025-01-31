"""Map from manufacturer to standard clusters for thermostatic valves."""

import logging
from typing import Optional, Union

from zigpy.profiles import zha
import zigpy.types as t
from zigpy.zcl import foundation
from zigpy.zcl.clusters.general import (
    AnalogOutput,
    Basic,
    BinaryInput,
    Groups,
    Identify,
    OnOff,
    Ota,
    PowerConfiguration,
    Scenes,
    Time,
)
from zigpy.zcl.clusters.hvac import Thermostat

from zhaquirks import Bus, LocalDataCluster
from zhaquirks.const import (
    DEVICE_TYPE,
    ENDPOINTS,
    INPUT_CLUSTERS,
    MODELS_INFO,
    OUTPUT_CLUSTERS,
    PROFILE_ID,
)
from zhaquirks.tuya import (
    TuyaManufCluster,
    TuyaManufClusterAttributes,
    TuyaPowerConfigurationCluster2AA,
    TuyaThermostat,
    TuyaThermostatCluster,
    TuyaTimePayload,
    TuyaUserInterfaceCluster,
)
from zhaquirks.tuya.builder import TuyaQuirkBuilder
from zhaquirks.tuya.mcu import TuyaAttributesCluster

_LOGGER = logging.getLogger(__name__)

# info from https://github.com/Koenkk/zigbee-herdsman-converters/blob/master/converters/common.js#L113
# and https://github.com/Koenkk/zigbee-herdsman-converters/blob/master/converters/fromZigbee.js#L362
SITERWELL_CHILD_LOCK_ATTR = 0x0107  # [0] unlocked [1] child-locked
SITERWELL_WINDOW_DETECT_ATTR = 0x0112  # [0] inactive [1] active
SITERWELL_VALVE_DETECT_ATTR = 0x0114  # [0] do not report [1] report
SITERWELL_VALVE_STATE_ATTR = 0x026D  # [0,0,0,55] opening percentage
SITERWELL_TARGET_TEMP_ATTR = 0x0202  # [0,0,0,210] target room temp (decidegree)
SITERWELL_TEMPERATURE_ATTR = 0x0203  # [0,0,0,200] current room temp (decidegree)
SITERWELL_BATTERY_ATTR = 0x0215  # [0,0,0,98] battery charge
SITERWELL_MODE_ATTR = 0x0404  # [0] off [1] scheduled [2] manual


class TuyaThermostatSystemMode(t.enum8):
    """Tuya thermostat system mode enum."""

    Auto = 0x00
    Heat = 0x01
    Off = 0x02


class TuyaThermostatV2(Thermostat, TuyaAttributesCluster):
    """Tuya local thermostat cluster."""

    manufacturer_id_override: t.uint16_t = foundation.ZCLHeader.NO_MANUFACTURER_ID

    _CONSTANT_ATTRIBUTES = {
        Thermostat.AttributeDefs.ctrl_sequence_of_oper.id: Thermostat.ControlSequenceOfOperation.Heating_Only
    }

    def __init__(self, *args, **kwargs):
        """Init a TuyaThermostat cluster."""
        super().__init__(*args, **kwargs)
        self.add_unsupported_attribute(
            Thermostat.AttributeDefs.setpoint_change_source.id
        )
        self.add_unsupported_attribute(
            Thermostat.AttributeDefs.setpoint_change_source_timestamp.id
        )
        self.add_unsupported_attribute(Thermostat.AttributeDefs.pi_heating_demand.id)

    async def write_attributes(self, attributes, manufacturer=None):
        """Overwrite to force manufacturer code."""

        return await super().write_attributes(
            attributes, manufacturer=foundation.ZCLHeader.NO_MANUFACTURER_ID
        )


class SiterwellManufCluster(TuyaManufClusterAttributes):
    """Manufacturer Specific Cluster of some thermostatic valves."""

    set_time_offset = 1970

    attributes = TuyaManufClusterAttributes.attributes.copy()
    attributes.update(
        {
            SITERWELL_CHILD_LOCK_ATTR: ("child_lock", t.uint8_t, True),
            SITERWELL_WINDOW_DETECT_ATTR: ("window_detection", t.uint8_t, True),
            SITERWELL_VALVE_DETECT_ATTR: ("valve_detect", t.uint8_t, True),
            SITERWELL_VALVE_STATE_ATTR: ("valve_state", t.uint32_t, True),
            SITERWELL_TARGET_TEMP_ATTR: ("target_temperature", t.uint32_t, True),
            SITERWELL_TEMPERATURE_ATTR: ("temperature", t.uint32_t, True),
            SITERWELL_BATTERY_ATTR: ("battery", t.uint32_t, True),
            SITERWELL_MODE_ATTR: ("mode", t.uint8_t, True),
        }
    )

    TEMPERATURE_ATTRS = {
        SITERWELL_TEMPERATURE_ATTR: "local_temperature",
        SITERWELL_TARGET_TEMP_ATTR: "occupied_heating_setpoint",
    }

    def _update_attribute(self, attrid, value):
        super()._update_attribute(attrid, value)
        if attrid in self.TEMPERATURE_ATTRS:
            self.endpoint.device.thermostat_bus.listener_event(
                "temperature_change",
                self.TEMPERATURE_ATTRS[attrid],
                value * 10,  # decidegree to centidegree
            )
        elif attrid == SITERWELL_MODE_ATTR:
            self.endpoint.device.thermostat_bus.listener_event("mode_change", value)
            self.endpoint.device.thermostat_bus.listener_event(
                "state_change", value > 0
            )
        elif attrid == SITERWELL_VALVE_STATE_ATTR:
            self.endpoint.device.thermostat_bus.listener_event("state_change", value)
        elif attrid == SITERWELL_CHILD_LOCK_ATTR:
            mode = 1 if value else 0
            self.endpoint.device.ui_bus.listener_event("child_lock_change", mode)
        elif attrid == SITERWELL_BATTERY_ATTR:
            self.endpoint.device.battery_bus.listener_event("battery_change", value)


class SiterwellThermostat(TuyaThermostatCluster):
    """Thermostat cluster for some thermostatic valves."""

    def map_attribute(self, attribute, value):
        """Map standardized attribute value to dict of manufacturer values."""

        if attribute == "occupied_heating_setpoint":
            # centidegree to decidegree
            return {SITERWELL_TARGET_TEMP_ATTR: round(value / 10)}
        if attribute in ("system_mode", "programing_oper_mode"):
            if attribute == "system_mode":
                system_mode = value
                oper_mode = self._attr_cache.get(
                    self.attributes_by_name["programing_oper_mode"].id,
                    self.ProgrammingOperationMode.Simple,
                )
            else:
                system_mode = self._attr_cache.get(
                    self.attributes_by_name["system_mode"].id, self.SystemMode.Heat
                )
                oper_mode = value
            if system_mode == self.SystemMode.Off:
                return {SITERWELL_MODE_ATTR: 0}
            if system_mode == self.SystemMode.Heat:
                if oper_mode == self.ProgrammingOperationMode.Schedule_programming_mode:
                    return {SITERWELL_MODE_ATTR: 1}
                if oper_mode == self.ProgrammingOperationMode.Simple:
                    return {SITERWELL_MODE_ATTR: 2}
                self.error("Unsupported value for ProgrammingOperationMode")
            else:
                self.error("Unsupported value for SystemMode")

    def mode_change(self, value):
        """System Mode change."""
        if value == 0:
            self._update_attribute(
                self.attributes_by_name["system_mode"].id, self.SystemMode.Off
            )
            return

        if value == 1:
            mode = self.ProgrammingOperationMode.Schedule_programming_mode
        else:
            mode = self.ProgrammingOperationMode.Simple

        self._update_attribute(
            self.attributes_by_name["system_mode"].id, self.SystemMode.Heat
        )
        self._update_attribute(self.attributes_by_name["programing_oper_mode"].id, mode)


class SiterwellUserInterface(TuyaUserInterfaceCluster):
    """HVAC User interface cluster for tuya electric heating thermostats."""

    _CHILD_LOCK_ATTR = SITERWELL_CHILD_LOCK_ATTR


# info from https://github.com/Koenkk/zigbee-herdsman-converters/blob/master/lib/tuya.js
# and https://github.com/Koenkk/zigbee-herdsman-converters/blob/master/converters/fromZigbee.js#L2777
MOES_TARGET_TEMP_ATTR = 0x0202  # target room temp (decidegree)
MOES_TEMPERATURE_ATTR = 0x0203  # current room temp (decidegree)
MOES_MODE_ATTR = 0x0404  # [0] away [1] scheduled [2] manual [3] comfort [4] eco [5] boost [6] complex
MOES_CHILD_LOCK_ATTR = 0x0107  # [0] unlocked [1] child-locked
MOES_VALVE_DETECT_ATTR = 0x0114  # [0] do not report [1] report
MOES_TEMP_CALIBRATION_ATTR = 0x022C  # temperature calibration (decidegree)
MOES_MIN_TEMPERATURE_ATTR = 0x0266  # minimum limit of temperature setting (decidegree)
MOES_MAX_TEMPERATURE_ATTR = 0x0267  # maximum limit of temperature setting (decidegree)
MOES_WINDOW_DETECT_ATTR = 0x0068  # [0,35,5] on/off, temperature, operating time (min)
MOES_BOOST_TIME_ATTR = 0x0269  # BOOST mode operating time in (sec)
MOES_FORCE_VALVE_ATTR = 0x046A  # [0] normal [1] open [2] close
MOES_COMFORT_TEMP_ATTR = 0x026B  # comfort mode temperaure (decidegree)
MOES_ECO_TEMP_ATTR = 0x026C  # eco mode temperature (decidegree)
MOES_VALVE_STATE_ATTR = 0x026D  # opening percentage
MOES_BATTERY_LOW_ATTR = 0x016E  # battery low warning
MOES_WEEK_FORMAT_ATTR = 0x046F  # [0] 5 days [1] 6 days, [2] 7 days
MOES_AWAY_TEMP_ATTR = 0x0272  # away mode temperature (decidegree)
MOES_AUTO_LOCK_ATTR = 0x0174  # [0] auto [1] manual
MOES_AWAY_DAYS_ATTR = 0x0275  # away mode duration (days)

# schedule [6,0,20,8,0,15,11,30,15,12,30,15,17,30,20,22,0,15]
# 6:00 - 20*, 8:00 - 15*, 11:30 - 15*, 12:30 - 15*, 17:30 - 20*, 22:00 - 15*
# Top bits in hours have special meaning
#   8: ??
#   7: Current schedule indicator
MOES_SCHEDULE_WORKDAY_ATTR = 0x0070
MOES_SCHEDULE_WEEKEND_ATTR = 0x0071


class data144(t.FixedList, item_type=t.uint8_t, length=18):
    """General data, Discrete, 144 bit."""


class MoesManufCluster(TuyaManufClusterAttributes):
    """Manufacturer Specific Cluster of some thermostatic valves."""

    set_time_offset = 1970

    attributes = TuyaManufClusterAttributes.attributes.copy()
    attributes.update(
        {
            MOES_CHILD_LOCK_ATTR: ("child_lock", t.uint8_t, True),
            MOES_WINDOW_DETECT_ATTR: ("window_detection", t.data24, True),
            MOES_VALVE_DETECT_ATTR: ("valve_detect", t.uint8_t, True),
            MOES_VALVE_STATE_ATTR: ("valve_state", t.uint32_t, True),
            MOES_TARGET_TEMP_ATTR: ("target_temperature", t.uint32_t, True),
            MOES_TEMPERATURE_ATTR: ("temperature", t.uint32_t, True),
            MOES_MODE_ATTR: ("mode", t.uint8_t, True),
            MOES_TEMP_CALIBRATION_ATTR: ("temperature_calibration", t.int32s, True),
            MOES_MIN_TEMPERATURE_ATTR: ("min_temperature", t.uint32_t, True),
            MOES_MAX_TEMPERATURE_ATTR: ("max_temperature", t.uint32_t, True),
            MOES_BOOST_TIME_ATTR: ("boost_duration_seconds", t.uint32_t, True),
            MOES_FORCE_VALVE_ATTR: ("valve_force_state", t.uint8_t, True),
            MOES_COMFORT_TEMP_ATTR: ("comfort_mode_temperature", t.uint32_t, True),
            MOES_ECO_TEMP_ATTR: ("eco_mode_temperature", t.uint32_t, True),
            MOES_BATTERY_LOW_ATTR: ("battery_low", t.uint8_t, True),
            MOES_WEEK_FORMAT_ATTR: ("week_format", t.uint8_t, True),
            MOES_AWAY_TEMP_ATTR: ("away_mode_temperature", t.uint32_t, True),
            MOES_AUTO_LOCK_ATTR: ("auto_lock", t.uint8_t, True),
            MOES_AWAY_DAYS_ATTR: ("away_duration_days", t.uint32_t, True),
            MOES_SCHEDULE_WORKDAY_ATTR: ("workday_schedule", data144, True),
            MOES_SCHEDULE_WEEKEND_ATTR: ("weekend_schedule", data144, True),
        }
    )

    DIRECT_MAPPED_ATTRS = {
        MOES_TEMPERATURE_ATTR: ("local_temperature", lambda value: value * 10),
        MOES_TARGET_TEMP_ATTR: ("occupied_heating_setpoint", lambda value: value * 10),
        MOES_AWAY_TEMP_ATTR: ("unoccupied_heating_setpoint", lambda value: value * 100),
        MOES_COMFORT_TEMP_ATTR: ("comfort_heating_setpoint", lambda value: value * 100),
        MOES_ECO_TEMP_ATTR: ("eco_heating_setpoint", lambda value: value * 100),
        MOES_TEMP_CALIBRATION_ATTR: (
            "local_temperature_calibration",
            lambda value: value * 10,
        ),
        MOES_MIN_TEMPERATURE_ATTR: (
            "min_heat_setpoint_limit",
            lambda value: value * 100,
        ),
        MOES_MAX_TEMPERATURE_ATTR: (
            "max_heat_setpoint_limit",
            lambda value: value * 100,
        ),
        MOES_VALVE_STATE_ATTR: ("valve_open_percentage", None),
        MOES_AWAY_DAYS_ATTR: ("unoccupied_duration_days", None),
        MOES_BOOST_TIME_ATTR: ("boost_duration_seconds", None),
        MOES_MODE_ATTR: ("operation_preset", None),
        MOES_WEEK_FORMAT_ATTR: ("work_days", None),
        MOES_FORCE_VALVE_ATTR: ("valve_force_state", None),
    }

    def _update_attribute(self, attrid, value):
        super()._update_attribute(attrid, value)
        if attrid in self.DIRECT_MAPPED_ATTRS:
            self.endpoint.device.thermostat_bus.listener_event(
                "temperature_change",
                self.DIRECT_MAPPED_ATTRS[attrid][0],
                value
                if self.DIRECT_MAPPED_ATTRS[attrid][1] is None
                else self.DIRECT_MAPPED_ATTRS[attrid][1](
                    value
                ),  # decidegree to centidegree
            )
        elif attrid in (MOES_SCHEDULE_WORKDAY_ATTR, MOES_SCHEDULE_WEEKEND_ATTR):
            self.endpoint.device.thermostat_bus.listener_event(
                "schedule_change", attrid, value
            )

        if attrid == MOES_WINDOW_DETECT_ATTR:
            self.endpoint.device.window_detection_bus.listener_event(
                "window_detect_change", value
            )
        elif attrid == MOES_MODE_ATTR:
            self.endpoint.device.thermostat_bus.listener_event("mode_change", value)
        elif attrid == MOES_VALVE_STATE_ATTR:
            self.endpoint.device.thermostat_bus.listener_event("state_change", value)
        elif attrid == MOES_CHILD_LOCK_ATTR:
            mode = 1 if value else 0
            self.endpoint.device.ui_bus.listener_event("child_lock_change", mode)
        elif attrid == MOES_AUTO_LOCK_ATTR:
            mode = 1 if value else 0
            self.endpoint.device.ui_bus.listener_event("autolock_change", mode)
        elif attrid == MOES_BATTERY_LOW_ATTR:
            self.endpoint.device.battery_bus.listener_event(
                "battery_change", 5 if value else 100
            )


class MoesManufClusterNew(MoesManufCluster):
    """Manufacturer Specific Cluster for the new _TZE200_b6wax7g0 thermostatic valves."""

    DIRECT_MAPPED_ATTRS = {
        MOES_TEMPERATURE_ATTR: ("local_temperature", lambda value: value * 10),
        MOES_TARGET_TEMP_ATTR: (
            "occupied_heating_setpoint",
            lambda value: value * 100,
        ),  # jms
        MOES_AWAY_TEMP_ATTR: ("unoccupied_heating_setpoint", lambda value: value * 100),
        MOES_COMFORT_TEMP_ATTR: ("comfort_heating_setpoint", lambda value: value * 100),
        MOES_ECO_TEMP_ATTR: ("eco_heating_setpoint", lambda value: value * 100),
        MOES_TEMP_CALIBRATION_ATTR: (
            "local_temperature_calibration",
            lambda value: value * 10,
        ),
        MOES_MIN_TEMPERATURE_ATTR: (
            "min_heat_setpoint_limit",
            lambda value: value * 100,
        ),
        MOES_MAX_TEMPERATURE_ATTR: (
            "max_heat_setpoint_limit",
            lambda value: value * 100,
        ),
        MOES_VALVE_STATE_ATTR: ("valve_open_percentage", None),
        MOES_AWAY_DAYS_ATTR: ("unoccupied_duration_days", None),
        MOES_BOOST_TIME_ATTR: ("boost_duration_seconds", None),
        MOES_MODE_ATTR: ("operation_preset", None),
        MOES_WEEK_FORMAT_ATTR: ("work_days", None),
        MOES_FORCE_VALVE_ATTR: ("valve_force_state", None),
    }


class MoesThermostat(TuyaThermostatCluster):
    """Thermostat cluster for some thermostatic valves."""

    class Preset(t.enum8):
        """Working modes of the thermostat."""

        Away = 0x00
        Schedule = 0x01
        Manual = 0x02
        Comfort = 0x03
        Eco = 0x04
        Boost = 0x05
        Complex = 0x06

    class WorkDays(t.enum8):
        """Workday configuration for scheduler operation mode."""

        MonToFri = 0x00
        MonToSat = 0x01
        MonToSun = 0x02

    class ForceValveState(t.enum8):
        """Force valve state option."""

        Normal = 0x00
        Open = 0x01
        Close = 0x02

    _CONSTANT_ATTRIBUTES = {
        0x001B: Thermostat.ControlSequenceOfOperation.Heating_Only,
        0x001C: Thermostat.SystemMode.Heat,
    }

    attributes = TuyaThermostatCluster.attributes.copy()
    attributes.update(
        {
            0x4000: ("comfort_heating_setpoint", t.int16s, True),
            0x4001: ("eco_heating_setpoint", t.int16s, True),
            0x4002: ("operation_preset", Preset, True),
            0x4003: ("work_days", WorkDays, True),
            0x4004: ("valve_open_percentage", t.uint8_t, True),
            0x4005: ("boost_duration_seconds", t.uint32_t, True),
            0x4006: ("valve_force_state", ForceValveState, True),
            0x4007: ("unoccupied_duration_days", t.uint32_t, True),
            0x4110: ("workday_schedule_1_hour", t.uint8_t, True),
            0x4111: ("workday_schedule_1_minute", t.uint8_t, True),
            0x4112: ("workday_schedule_1_temperature", t.int16s, True),
            0x4120: ("workday_schedule_2_hour", t.uint8_t, True),
            0x4121: ("workday_schedule_2_minute", t.uint8_t, True),
            0x4122: ("workday_schedule_2_temperature", t.int16s, True),
            0x4130: ("workday_schedule_3_hour", t.uint8_t, True),
            0x4131: ("workday_schedule_3_minute", t.uint8_t, True),
            0x4132: ("workday_schedule_3_temperature", t.int16s, True),
            0x4140: ("workday_schedule_4_hour", t.uint8_t, True),
            0x4141: ("workday_schedule_4_minute", t.uint8_t, True),
            0x4142: ("workday_schedule_4_temperature", t.int16s, True),
            0x4150: ("workday_schedule_5_hour", t.uint8_t, True),
            0x4151: ("workday_schedule_5_minute", t.uint8_t, True),
            0x4152: ("workday_schedule_5_temperature", t.int16s, True),
            0x4160: ("workday_schedule_6_hour", t.uint8_t, True),
            0x4161: ("workday_schedule_6_minute", t.uint8_t, True),
            0x4162: ("workday_schedule_6_temperature", t.int16s, True),
            0x4210: ("weekend_schedule_1_hour", t.uint8_t, True),
            0x4211: ("weekend_schedule_1_minute", t.uint8_t, True),
            0x4212: ("weekend_schedule_1_temperature", t.int16s, True),
            0x4220: ("weekend_schedule_2_hour", t.uint8_t, True),
            0x4221: ("weekend_schedule_2_minute", t.uint8_t, True),
            0x4222: ("weekend_schedule_2_temperature", t.int16s, True),
            0x4230: ("weekend_schedule_3_hour", t.uint8_t, True),
            0x4231: ("weekend_schedule_3_minute", t.uint8_t, True),
            0x4232: ("weekend_schedule_3_temperature", t.int16s, True),
            0x4240: ("weekend_schedule_4_hour", t.uint8_t, True),
            0x4241: ("weekend_schedule_4_minute", t.uint8_t, True),
            0x4242: ("weekend_schedule_4_temperature", t.int16s, True),
            0x4250: ("weekend_schedule_5_hour", t.uint8_t, True),
            0x4251: ("weekend_schedule_5_minute", t.uint8_t, True),
            0x4252: ("weekend_schedule_5_temperature", t.int16s, True),
            0x4260: ("weekend_schedule_6_hour", t.uint8_t, True),
            0x4261: ("weekend_schedule_6_minute", t.uint8_t, True),
            0x4262: ("weekend_schedule_6_temperature", t.int16s, True),
        }
    )

    DIRECT_MAPPING_ATTRS = {
        "occupied_heating_setpoint": (
            MOES_TARGET_TEMP_ATTR,
            lambda value: round(value / 10),
        ),
        "unoccupied_heating_setpoint": (
            MOES_AWAY_TEMP_ATTR,
            lambda value: round(value / 100),
        ),
        "comfort_heating_setpoint": (
            MOES_COMFORT_TEMP_ATTR,
            lambda value: round(value / 100),
        ),
        "eco_heating_setpoint": (MOES_ECO_TEMP_ATTR, lambda value: round(value / 100)),
        "min_heat_setpoint_limit": (
            MOES_MIN_TEMPERATURE_ATTR,
            lambda value: round(value / 100),
        ),
        "max_heat_setpoint_limit": (
            MOES_MAX_TEMPERATURE_ATTR,
            lambda value: round(value / 100),
        ),
        "local_temperature_calibration": (
            MOES_TEMP_CALIBRATION_ATTR,
            lambda value: round(value / 10),
        ),
        "work_days": (MOES_WEEK_FORMAT_ATTR, None),
        "operation_preset": (MOES_MODE_ATTR, None),
        "boost_duration_seconds": (MOES_BOOST_TIME_ATTR, None),
        "valve_force_state": (MOES_FORCE_VALVE_ATTR, None),
        "unoccupied_duration_days": (MOES_AWAY_DAYS_ATTR, None),
    }

    WORKDAY_SCHEDULE_ATTRS = {
        "workday_schedule_6_temperature": 1500,
        "workday_schedule_6_minute": 0,
        "workday_schedule_6_hour": 22,
        "workday_schedule_5_temperature": 2000,
        "workday_schedule_5_minute": 30,
        "workday_schedule_5_hour": 17,
        "workday_schedule_4_temperature": 1500,
        "workday_schedule_4_minute": 30,
        "workday_schedule_4_hour": 12,
        "workday_schedule_3_temperature": 1500,
        "workday_schedule_3_minute": 30,
        "workday_schedule_3_hour": 11,
        "workday_schedule_2_temperature": 1500,
        "workday_schedule_2_minute": 0,
        "workday_schedule_2_hour": 8,
        "workday_schedule_1_temperature": 2000,
        "workday_schedule_1_minute": 0,
        "workday_schedule_1_hour": 6,
    }

    WEEKEND_SCHEDULE_ATTRS = {
        "weekend_schedule_6_temperature": 1500,
        "weekend_schedule_6_minute": 0,
        "weekend_schedule_6_hour": 22,
        "weekend_schedule_5_temperature": 2000,
        "weekend_schedule_5_minute": 30,
        "weekend_schedule_5_hour": 17,
        "weekend_schedule_4_temperature": 1500,
        "weekend_schedule_4_minute": 30,
        "weekend_schedule_4_hour": 12,
        "weekend_schedule_3_temperature": 1500,
        "weekend_schedule_3_minute": 30,
        "weekend_schedule_3_hour": 11,
        "weekend_schedule_2_temperature": 1500,
        "weekend_schedule_2_minute": 0,
        "weekend_schedule_2_hour": 8,
        "weekend_schedule_1_temperature": 2000,
        "weekend_schedule_1_minute": 0,
        "weekend_schedule_1_hour": 6,
    }

    def map_attribute(self, attribute, value):
        """Map standardized attribute value to dict of manufacturer values."""

        if attribute in self.DIRECT_MAPPING_ATTRS:
            return {
                self.DIRECT_MAPPING_ATTRS[attribute][0]: value
                if self.DIRECT_MAPPING_ATTRS[attribute][1] is None
                else self.DIRECT_MAPPING_ATTRS[attribute][1](value)
            }
        if attribute in ("programing_oper_mode", "occupancy"):
            if attribute == "occupancy":
                occupancy = value
                oper_mode = self._attr_cache.get(
                    self.attributes_by_name["programing_oper_mode"].id,
                    self.ProgrammingOperationMode.Simple,
                )
            else:
                occupancy = self._attr_cache.get(
                    self.attributes_by_name["occupancy"].id, self.Occupancy.Occupied
                )
                oper_mode = value
            if occupancy == self.Occupancy.Unoccupied:
                return {MOES_MODE_ATTR: 0}
            if occupancy == self.Occupancy.Occupied:
                if oper_mode == self.ProgrammingOperationMode.Schedule_programming_mode:
                    return {MOES_MODE_ATTR: 1}
                if oper_mode == self.ProgrammingOperationMode.Simple:
                    return {MOES_MODE_ATTR: 2}
                if oper_mode == self.ProgrammingOperationMode.Economy_mode:
                    return {MOES_MODE_ATTR: 4}
                self.error("Unsupported value for ProgrammingOperationMode")
            else:
                self.error("Unsupported value for Occupancy")
        if attribute == "system_mode":
            return {
                MOES_MODE_ATTR: self._attr_cache.get(
                    self.attributes_by_name["operation_preset"].id, 2
                )
            }
        if attribute in self.WORKDAY_SCHEDULE_ATTRS:
            data = data144()
            for num, (attr, default) in enumerate(self.WORKDAY_SCHEDULE_ATTRS.items()):
                if num % 3 == 0:
                    if attr == attribute:
                        val = round(value / 100)
                    else:
                        val = round(
                            self._attr_cache.get(
                                self.attributes_by_name[attr].id, default
                            )
                            / 100
                        )
                elif attr == attribute:
                    val = value
                else:
                    val = self._attr_cache.get(
                        self.attributes_by_name[attr].id, default
                    )

                data.append(val)
            return {MOES_SCHEDULE_WORKDAY_ATTR: data}
        if attribute in self.WEEKEND_SCHEDULE_ATTRS:
            data = data144()
            for num, (attr, default) in enumerate(self.WEEKEND_SCHEDULE_ATTRS.items()):
                if num % 3 == 0:
                    if attr == attribute:
                        val = round(value / 100)
                    else:
                        val = round(
                            self._attr_cache.get(
                                self.attributes_by_name[attr].id, default
                            )
                            / 100
                        )
                elif attr == attribute:
                    val = value
                else:
                    val = self._attr_cache.get(
                        self.attributes_by_name[attr].id, default
                    )

                data.append(val)
            return {MOES_SCHEDULE_WEEKEND_ATTR: data}

    def mode_change(self, value):
        """System Mode change."""
        if value == 0:
            prog_mode = self.ProgrammingOperationMode.Simple
            occupancy = self.Occupancy.Unoccupied
        elif value == 1:
            prog_mode = self.ProgrammingOperationMode.Schedule_programming_mode
            occupancy = self.Occupancy.Occupied
        elif value in (2, 3):
            prog_mode = self.ProgrammingOperationMode.Simple
            occupancy = self.Occupancy.Occupied
        elif value == 4:
            prog_mode = self.ProgrammingOperationMode.Economy_mode
            occupancy = self.Occupancy.Occupied
        elif value == 5:
            prog_mode = self.ProgrammingOperationMode.Simple
            occupancy = self.Occupancy.Occupied
        else:
            prog_mode = self.ProgrammingOperationMode.Simple
            occupancy = self.Occupancy.Occupied

        self._update_attribute(
            self.attributes_by_name["programing_oper_mode"].id, prog_mode
        )
        self._update_attribute(self.attributes_by_name["occupancy"].id, occupancy)

    def schedule_change(self, attr, value):
        """Scheduler attribute change."""

        if attr == MOES_SCHEDULE_WORKDAY_ATTR:
            self._update_attribute(
                self.attributes_by_name["workday_schedule_1_hour"].id, value[17] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_1_minute"].id, value[16]
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_1_temperature"].id,
                value[15] * 100,
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_2_hour"].id, value[14] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_2_minute"].id, value[13]
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_2_temperature"].id,
                value[12] * 100,
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_3_hour"].id, value[11] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_3_minute"].id, value[10]
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_3_temperature"].id,
                value[9] * 100,
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_4_hour"].id, value[8] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_4_minute"].id, value[7]
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_4_temperature"].id,
                value[6] * 100,
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_5_hour"].id, value[5] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_5_minute"].id, value[4]
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_5_temperature"].id,
                value[3] * 100,
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_6_hour"].id, value[2] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_6_minute"].id, value[1]
            )
            self._update_attribute(
                self.attributes_by_name["workday_schedule_6_temperature"].id,
                value[0] * 100,
            )
        elif attr == MOES_SCHEDULE_WEEKEND_ATTR:
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_1_hour"].id, value[17] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_1_minute"].id, value[16]
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_1_temperature"].id,
                value[15] * 100,
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_2_hour"].id, value[14] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_2_minute"].id, value[13]
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_2_temperature"].id,
                value[12] * 100,
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_3_hour"].id, value[11] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_3_minute"].id, value[10]
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_3_temperature"].id,
                value[9] * 100,
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_4_hour"].id, value[8] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_4_minute"].id, value[7]
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_4_temperature"].id,
                value[6] * 100,
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_5_hour"].id, value[5] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_5_minute"].id, value[4]
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_5_temperature"].id,
                value[3] * 100,
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_6_hour"].id, value[2] & 0x3F
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_6_minute"].id, value[1]
            )
            self._update_attribute(
                self.attributes_by_name["weekend_schedule_6_temperature"].id,
                value[0] * 100,
            )


class MoesThermostatNew(MoesThermostat):
    """Thermostat cluster for the new _TZE200_b6wax7g0 thermostatic valve."""

    DIRECT_MAPPING_ATTRS = {
        "occupied_heating_setpoint": (
            MOES_TARGET_TEMP_ATTR,
            lambda value: round(value / 100),  # jms
        ),
        "unoccupied_heating_setpoint": (
            MOES_AWAY_TEMP_ATTR,
            lambda value: round(value / 100),
        ),
        "comfort_heating_setpoint": (
            MOES_COMFORT_TEMP_ATTR,
            lambda value: round(value / 100),
        ),
        "eco_heating_setpoint": (MOES_ECO_TEMP_ATTR, lambda value: round(value / 100)),
        "min_heat_setpoint_limit": (
            MOES_MIN_TEMPERATURE_ATTR,
            lambda value: round(value / 100),
        ),
        "max_heat_setpoint_limit": (
            MOES_MAX_TEMPERATURE_ATTR,
            lambda value: round(value / 100),
        ),
        "local_temperature_calibration": (
            MOES_TEMP_CALIBRATION_ATTR,
            lambda value: round(value / 10),
        ),
        "work_days": (MOES_WEEK_FORMAT_ATTR, None),
        "operation_preset": (MOES_MODE_ATTR, None),
        "boost_duration_seconds": (MOES_BOOST_TIME_ATTR, None),
        "valve_force_state": (MOES_FORCE_VALVE_ATTR, None),
        "unoccupied_duration_days": (MOES_AWAY_DAYS_ATTR, None),
    }


class MoesUserInterface(TuyaUserInterfaceCluster):
    """HVAC User interface cluster for tuya electric heating thermostats."""

    _CHILD_LOCK_ATTR = MOES_CHILD_LOCK_ATTR

    attributes = TuyaUserInterfaceCluster.attributes.copy()
    attributes.update(
        {
            0x5000: ("auto_lock", t.Bool, True),
        }
    )

    def autolock_change(self, value):
        """Automatic lock change."""

        self._update_attribute(self.attributes_by_name["auto_lock"].id, value)

    def map_attribute(self, attribute, value):
        """Map standardized attribute value to dict of manufacturer values."""

        if attribute == "auto_lock":
            return {MOES_AUTO_LOCK_ATTR: value}


class MoesWindowDetection(LocalDataCluster, OnOff):
    """On/Off cluster for the window detection function of the electric heating thermostats."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.window_detection_bus.add_listener(self)

    attributes = OnOff.attributes.copy()
    attributes.update(
        {
            0x6000: ("window_detection_temperature", t.int16s, True),
            0x6001: ("window_detection_timeout_minutes", t.uint8_t, True),
        }
    )

    def window_detect_change(self, value):
        """Window detection change."""

        self._update_attribute(
            self.attributes_by_name["window_detection_timeout_minutes"].id, value[0]
        )
        self._update_attribute(
            self.attributes_by_name["window_detection_temperature"].id, value[1] * 100
        )
        self._update_attribute(self.attributes_by_name["on_off"].id, value[2])

    async def write_attributes(self, attributes, manufacturer=None, **kwargs):
        """Defer attributes writing to the set_data tuya command."""

        records = self._write_attr_records(attributes)

        if not records:
            return [[foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)]]

        has_change = False
        data = t.data24()
        data.append(
            self._attr_cache.get(
                self.attributes_by_name["window_detection_timeout_minutes"].id,
                5,
            )
        )
        data.append(
            round(
                self._attr_cache.get(
                    self.attributes_by_name["window_detection_temperature"].id,
                    50,
                )
                / 100
            )
        )
        data.append(
            self._attr_cache.get(
                self.attributes_by_name["on_off"].id,
                False,
            )
        )

        for record in records:
            attr_name = self.attributes[record.attrid].name
            if attr_name == "on_off":
                data[2] = record.value.value
                has_change = True
            elif attr_name == "window_detection_temperature":
                data[1] = record.value.value / 100
                has_change = True
            elif attr_name == "window_detection_timeout_minutes":
                data[0] = record.value.value
                has_change = True

        if has_change:
            return await self.endpoint.tuya_manufacturer.write_attributes(
                {MOES_WINDOW_DETECT_ATTR: data}, manufacturer=manufacturer
            )

        return [
            [
                foundation.WriteAttributesStatusRecord(
                    foundation.Status.FAILURE, r.attrid
                )
                for r in records
            ]
        ]

    async def command(
        self,
        command_id: Union[foundation.GeneralCommand, int, t.uint8_t],
        *args,
        manufacturer: Optional[Union[int, t.uint16_t]] = None,
        expect_reply: bool = True,
        tsn: Optional[Union[int, t.uint8_t]] = None,
    ):
        """Override the default Cluster command."""

        if command_id in (0x0000, 0x0001, 0x0002):
            if command_id == 0x0000:
                value = False
            elif command_id == 0x0001:
                value = True
            else:
                attrid = self.attributes_by_name["on_off"].id
                success, _ = await self.read_attributes(
                    (attrid,), manufacturer=manufacturer
                )
                try:
                    value = success[attrid]
                except KeyError:
                    return foundation.GENERAL_COMMANDS[
                        foundation.GeneralCommand.Default_Response
                    ].schema(command_id=command_id, status=foundation.Status.FAILURE)
                value = not value

            (res,) = await self.write_attributes(
                {"on_off": value},
                manufacturer=manufacturer,
            )
            return foundation.GENERAL_COMMANDS[
                foundation.GeneralCommand.Default_Response
            ].schema(command_id=command_id, status=res[0].status)

        return foundation.GENERAL_COMMANDS[
            foundation.GeneralCommand.Default_Response
        ].schema(command_id=command_id, status=foundation.Status.UNSUP_CLUSTER_COMMAND)


ZONNSMART_MODE_ATTR = (
    0x0402  # [0] Scheduled/auto [1] manual [2] Holiday [3] HolidayTempShow
)
ZONNSMART_WINDOW_DETECT_ATTR = 0x0108  # window is opened [0] false [1] true
ZONNSMART_FROST_PROTECT_ATTR = 0x010A  # [0] inactive [1] active
ZONNSMART_TARGET_TEMP_ATTR = 0x0210  # [0,0,0,210] target room temp (decidegree)
ZONNSMART_TEMPERATURE_ATTR = 0x0218  # [0,0,0,200] current room temp (decidegree)
ZONNSMART_TEMPERATURE_CALIBRATION_ATTR = 0x021B  # temperature calibration (decidegree)
ZONNSMART_WEEK_FORMAT_ATTR = 0x041F  # # [0] 5+2 days [1] 6+1 days, [2] 7 days
ZONNSMART_HOLIDAY_TEMP_ATTR = (
    0x0220  # [0, 0, 0, 170] temp in holiday mode (decidegreee)
)
ZONNSMART_BATTERY_ATTR = 0x0223  # [0,0,0,98] battery charge
ZONNSMART_UPTIME_TIME_ATTR = (
    0x0024  # Seems to be the uptime attribute (sent hourly, increases) [0,200]
)
ZONNSMART_CHILD_LOCK_ATTR = 0x0128  # [0] unlocked [1] child-locked
ZONNSMART_FAULT_DETECTION_ATTR = 0x052D  # [0] no fault [1] fault detected
ZONNSMART_HOLIDAY_DATETIME_ATTR = 0x032E  # holiday mode datetime of begin and end
ZONNSMART_BOOST_TIME_ATTR = 0x0265  # BOOST mode operating time in (sec) [0, 0, 1, 44]
ZONNSMART_OPENED_WINDOW_TEMP = 0x0266  # [0, 0, 0, 210] opened window detected temp
ZONNSMART_COMFORT_TEMP_ATTR = 0x0268  # [0, 0, 0, 210] comfort temp in auto (decidegree)
ZONNSMART_ECO_TEMP_ATTR = 0x0269  # [0, 0, 0, 170] eco temp in auto (decidegree)
ZONNSMART_HEATING_STOPPING_ATTR = 0x016B  # [0] inactive [1] active
# In online mode TRV publishes all values, expires automatically after ca. 1 min
# TRV uses different datatype for send and receive, we need both
ZONNSMART_ONLINE_MODE_ENUM_ATTR = 0x0473  # device publises value as enum datatype
ZONNSMART_ONLINE_MODE_BOOL_ATTR = 0x0173  # but expects to receive bool datatype

ZONNSMART_MAX_TEMPERATURE_VAL = 3000
ZONNSMART_MIN_TEMPERATURE_VAL = 500
ZonnsmartManuClusterSelf = None


class ZONNSMARTManufCluster(TuyaManufClusterAttributes):
    """Manufacturer Specific Cluster of some thermostatic valves."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        global ZonnsmartManuClusterSelf  # noqa: PLW0603
        ZonnsmartManuClusterSelf = self

    attributes = TuyaManufClusterAttributes.attributes.copy()
    attributes.update(
        {
            ZONNSMART_MODE_ATTR: ("mode", t.uint8_t, True),
            ZONNSMART_WINDOW_DETECT_ATTR: ("window_detection", t.uint8_t, True),
            ZONNSMART_FROST_PROTECT_ATTR: ("frost_protection", t.uint8_t, True),
            ZONNSMART_TARGET_TEMP_ATTR: ("target_temperature", t.uint32_t, True),
            ZONNSMART_TEMPERATURE_ATTR: ("temperature", t.uint32_t, True),
            ZONNSMART_TEMPERATURE_CALIBRATION_ATTR: (
                "temperature_calibration",
                t.int32s,
                True,
            ),
            ZONNSMART_WEEK_FORMAT_ATTR: ("week_format", t.uint8_t, True),
            ZONNSMART_HOLIDAY_TEMP_ATTR: ("holiday_temperature", t.uint32_t, True),
            ZONNSMART_BATTERY_ATTR: ("battery", t.uint32_t, True),
            ZONNSMART_UPTIME_TIME_ATTR: ("uptime", t.uint32_t, True),
            ZONNSMART_CHILD_LOCK_ATTR: ("child_lock", t.uint8_t, True),
            ZONNSMART_FAULT_DETECTION_ATTR: ("fault_detected", t.uint8_t, True),
            ZONNSMART_BOOST_TIME_ATTR: ("boost_duration_seconds", t.uint32_t, True),
            ZONNSMART_OPENED_WINDOW_TEMP: (
                "opened_window_temperature",
                t.uint32_t,
                True,
            ),
            ZONNSMART_COMFORT_TEMP_ATTR: ("comfort_mode_temperature", t.uint32_t, True),
            ZONNSMART_ECO_TEMP_ATTR: ("eco_mode_temperature", t.uint32_t, True),
            ZONNSMART_HEATING_STOPPING_ATTR: ("heating_stop", t.uint8_t, True),
            ZONNSMART_ONLINE_MODE_BOOL_ATTR: ("online_set", t.uint8_t, True),
            ZONNSMART_ONLINE_MODE_ENUM_ATTR: ("online", t.uint8_t, True),
        }
    )

    DIRECT_MAPPED_ATTRS = {
        ZONNSMART_TEMPERATURE_ATTR: ("local_temperature", lambda value: value * 10),
        ZONNSMART_TEMPERATURE_CALIBRATION_ATTR: (
            "local_temperature_calibration",
            lambda value: value * 10,
        ),
        ZONNSMART_TARGET_TEMP_ATTR: (
            "occupied_heating_setpoint",
            lambda value: value * 10,
        ),
        ZONNSMART_HOLIDAY_TEMP_ATTR: (
            "unoccupied_heating_setpoint",
            lambda value: value * 10,
        ),
        ZONNSMART_FAULT_DETECTION_ATTR: (
            "alarm_mask",
            lambda value: 0x02 if value else 0x00,
        ),
    }

    def _update_attribute(self, attrid, value):
        super()._update_attribute(attrid, value)
        if attrid in self.DIRECT_MAPPED_ATTRS:
            self.endpoint.device.thermostat_bus.listener_event(
                "temperature_change",
                self.DIRECT_MAPPED_ATTRS[attrid][0],
                value
                if self.DIRECT_MAPPED_ATTRS[attrid][1] is None
                else self.DIRECT_MAPPED_ATTRS[attrid][1](value),
            )
        elif attrid == ZONNSMART_WINDOW_DETECT_ATTR:
            self.endpoint.device.window_detection_bus.listener_event("set_value", value)
        elif attrid == ZONNSMART_OPENED_WINDOW_TEMP:
            self.endpoint.device.window_temperature_bus.listener_event(
                "set_value", value
            )
        elif attrid in (ZONNSMART_MODE_ATTR, ZONNSMART_FROST_PROTECT_ATTR):
            self.endpoint.device.thermostat_bus.listener_event(
                "mode_change", attrid, value
            )
        elif attrid == ZONNSMART_HEATING_STOPPING_ATTR:
            self.endpoint.device.thermostat_bus.listener_event(
                "system_mode_change", value == 0
            )
        elif attrid == ZONNSMART_CHILD_LOCK_ATTR:
            self.endpoint.device.ui_bus.listener_event("child_lock_change", value)
            self.endpoint.device.child_lock_bus.listener_event("set_change", value)
        elif attrid == ZONNSMART_BATTERY_ATTR:
            self.endpoint.device.battery_bus.listener_event("battery_change", value)
        elif attrid == ZONNSMART_ONLINE_MODE_ENUM_ATTR:
            self.endpoint.device.online_mode_bus.listener_event("set_change", value)
        elif attrid == ZONNSMART_BOOST_TIME_ATTR:
            self.endpoint.device.boost_bus.listener_event(
                "set_change", 1 if value > 0 else 0
            )

        if attrid == ZONNSMART_TEMPERATURE_CALIBRATION_ATTR:
            self.endpoint.device.temperature_calibration_bus.listener_event(
                "set_value", value / 10
            )
        elif attrid in (ZONNSMART_TEMPERATURE_ATTR, ZONNSMART_TARGET_TEMP_ATTR):
            self.endpoint.device.thermostat_bus.listener_event(
                "state_temp_change", attrid, value
            )


class ZONNSMARTThermostat(TuyaThermostatCluster):
    """Thermostat cluster for some thermostatic valves."""

    class Preset(t.enum8):
        """Working modes of the thermostat."""

        Schedule = 0x00
        Manual = 0x01
        Holiday = 0x02
        HolidayTemp = 0x03
        FrostProtect = 0x04

    attributes = TuyaThermostatCluster.attributes.copy()
    attributes.update(
        {
            0x4002: ("operation_preset", Preset, True),
        }
    )

    DIRECT_MAPPING_ATTRS = {
        "occupied_heating_setpoint": (
            ZONNSMART_TARGET_TEMP_ATTR,
            lambda value: round(value / 10),
        ),
    }

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.thermostat_bus.listener_event(
            "temperature_change",
            "min_heat_setpoint_limit",
            ZONNSMART_MIN_TEMPERATURE_VAL,
        )
        self.endpoint.device.thermostat_bus.listener_event(
            "temperature_change",
            "max_heat_setpoint_limit",
            ZONNSMART_MAX_TEMPERATURE_VAL,
        )

    def map_attribute(self, attribute, value):
        """Map standardized attribute value to dict of manufacturer values."""
        if attribute in self.DIRECT_MAPPING_ATTRS:
            return {
                self.DIRECT_MAPPING_ATTRS[attribute][0]: value
                if self.DIRECT_MAPPING_ATTRS[attribute][1] is None
                else self.DIRECT_MAPPING_ATTRS[attribute][1](value)
            }
        elif attribute in ("system_mode", "programing_oper_mode"):
            if attribute == "system_mode":
                system_mode = value
                oper_mode = self._attr_cache.get(
                    self.attributes_by_name["programing_oper_mode"].id,
                    self.ProgrammingOperationMode.Simple,
                )
            else:
                system_mode = self._attr_cache.get(
                    self.attributes_by_name["system_mode"].id, self.SystemMode.Heat
                )
                oper_mode = value
            if system_mode == self.SystemMode.Off:
                return {ZONNSMART_HEATING_STOPPING_ATTR: 1}
            if system_mode == self.SystemMode.Heat:
                if oper_mode == self.ProgrammingOperationMode.Schedule_programming_mode:
                    return {ZONNSMART_MODE_ATTR: 0}
                if oper_mode == self.ProgrammingOperationMode.Simple:
                    return {ZONNSMART_MODE_ATTR: 1}
                self.error("Unsupported value for ProgrammingOperationMode")
            else:
                self.error("Unsupported value for SystemMode")
        elif attribute == "operation_preset":
            if value == 0:
                return {ZONNSMART_MODE_ATTR: 0}
            elif value == 1:
                return {ZONNSMART_MODE_ATTR: 1}
            elif value == 3:
                return {ZONNSMART_MODE_ATTR: 3}
            elif value == 4:
                return {ZONNSMART_FROST_PROTECT_ATTR: 1}
            else:
                self.error("Unsupported value for OperationPreset")

    def mode_change(self, attrid, value):
        """Mode change."""
        operation_preset = None

        if attrid == ZONNSMART_MODE_ATTR:
            prog_mode = None
            if value == 0:
                prog_mode = self.ProgrammingOperationMode.Schedule_programming_mode
                operation_preset = self.Preset.Schedule
            elif value == 1:
                prog_mode = self.ProgrammingOperationMode.Simple
                operation_preset = self.Preset.Manual
            elif value == 2:
                prog_mode = self.ProgrammingOperationMode.Simple
                operation_preset = self.Preset.Holiday
            elif value == 3:
                prog_mode = self.ProgrammingOperationMode.Schedule_programming_mode
                operation_preset = self.Preset.HolidayTemp
            else:
                self.error("Unsupported value for Mode")

            if prog_mode is not None:
                self._update_attribute(
                    self.attributes_by_name["programing_oper_mode"].id, prog_mode
                )
        elif attrid == ZONNSMART_FROST_PROTECT_ATTR:
            if value == 1:
                operation_preset = self.Preset.FrostProtect

        if operation_preset is not None:
            self._update_attribute(
                self.attributes_by_name["operation_preset"].id, operation_preset
            )

    def system_mode_change(self, value):
        """System Mode change."""
        self._update_attribute(
            self.attributes_by_name["system_mode"].id,
            self.SystemMode.Heat if value else self.SystemMode.Off,
        )

    def state_temp_change(self, attrid, value):
        """Set heating state based on current and set temperature."""
        if attrid == ZONNSMART_TEMPERATURE_ATTR:
            temp_current = value * 10
            temp_set = self._attr_cache.get(
                self.attributes_by_name["occupied_heating_setpoint"].id
            )
        elif attrid == ZONNSMART_TARGET_TEMP_ATTR:
            temp_current = self._attr_cache.get(
                self.attributes_by_name["local_temperature"].id
            )
            temp_set = value * 10
        else:
            return

        state = 0 if (int(temp_current) >= int(temp_set)) else 1
        self.endpoint.device.thermostat_bus.listener_event("state_change", state)


class ZONNSMARTUserInterface(TuyaUserInterfaceCluster):
    """HVAC User interface cluster for tuya electric heating thermostats."""

    _CHILD_LOCK_ATTR = ZONNSMART_CHILD_LOCK_ATTR


class ZONNSMARTWindowDetection(LocalDataCluster, BinaryInput):
    """Binary cluster for the window detection function of the heating thermostats."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.window_detection_bus.add_listener(self)
        self._update_attribute(
            self.attributes_by_name["description"].id, "Open Window Detected"
        )

    def set_value(self, value):
        """Set opened window value."""
        self._update_attribute(self.attributes_by_name["present_value"].id, value)


class ZONNSMARTHelperOnOff(LocalDataCluster, OnOff):
    """Helper OnOff cluster for various functions controlled by switch."""

    def set_change(self, value):
        """Set new OnOff value."""
        self._update_attribute(self.attributes_by_name["on_off"].id, value)

    def get_attr_val_to_write(self, value):
        """Return dict with attribute and value for thermostat."""
        return None

    async def write_attributes(self, attributes, manufacturer=None, **kwargs):
        """Defer attributes writing to the set_data tuya command."""
        records = self._write_attr_records(attributes)
        if not records:
            return [[foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)]]

        has_change = False
        for record in records:
            attr_name = self.attributes[record.attrid].name
            if attr_name == "on_off":
                value = record.value.value
                has_change = True

        if has_change:
            attr_val = self.get_attr_val_to_write(value)
            if attr_val is not None:
                # global self in case when different endpoint has to exist
                return await ZonnsmartManuClusterSelf.endpoint.tuya_manufacturer.write_attributes(
                    attr_val, manufacturer=manufacturer
                )

        return [
            [
                foundation.WriteAttributesStatusRecord(
                    foundation.Status.FAILURE, r.attrid
                )
                for r in records
            ]
        ]

    async def command(
        self,
        command_id: Union[foundation.GeneralCommand, int, t.uint8_t],
        *args,
        manufacturer: Optional[Union[int, t.uint16_t]] = None,
        expect_reply: bool = True,
        tsn: Optional[Union[int, t.uint8_t]] = None,
    ):
        """Override the default Cluster command."""

        if command_id in (0x0000, 0x0001, 0x0002):
            if command_id == 0x0000:
                value = False
            elif command_id == 0x0001:
                value = True
            else:
                attrid = self.attributes_by_name["on_off"].id
                success, _ = await self.read_attributes(
                    (attrid,), manufacturer=manufacturer
                )
                try:
                    value = success[attrid]
                except KeyError:
                    return foundation.GENERAL_COMMANDS[
                        foundation.GeneralCommand.Default_Response
                    ].schema(command_id=command_id, status=foundation.Status.FAILURE)
                value = not value
            _LOGGER.debug("CALLING WRITE FROM COMMAND")
            (res,) = await self.write_attributes(
                {"on_off": value},
                manufacturer=manufacturer,
            )
            return foundation.GENERAL_COMMANDS[
                foundation.GeneralCommand.Default_Response
            ].schema(command_id=command_id, status=res[0].status)

        return foundation.GENERAL_COMMANDS[
            foundation.GeneralCommand.Default_Response
        ].schema(command_id=command_id, status=foundation.Status.UNSUP_CLUSTER_COMMAND)


class ZONNSMARTBoost(ZONNSMARTHelperOnOff):
    """On/Off cluster for the boost function of the heating thermostats."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.boost_bus.add_listener(self)

    def get_attr_val_to_write(self, value):
        """Return dict with attribute and value for boot mode."""
        return {ZONNSMART_BOOST_TIME_ATTR: 299 if value else 0}


class ZONNSMARTChildLock(ZONNSMARTHelperOnOff):
    """On/Off cluster for the child lock of the heating thermostats."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.child_lock_bus.add_listener(self)

    def get_attr_val_to_write(self, value):
        """Return dict with attribute and value for child lock."""
        return {ZONNSMART_CHILD_LOCK_ATTR: value}


class ZONNSMARTOnlineMode(ZONNSMARTHelperOnOff):
    """On/Off cluster for the online mode of the heating thermostats."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.online_mode_bus.add_listener(self)

    def get_attr_val_to_write(self, value):
        """Return dict with attribute and value for online mode."""
        return {ZONNSMART_ONLINE_MODE_BOOL_ATTR: value}


class ZONNSMARTTemperatureOffset(LocalDataCluster, AnalogOutput):
    """AnalogOutput cluster for setting temperature offset."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.temperature_calibration_bus.add_listener(self)
        self._update_attribute(
            self.attributes_by_name["description"].id, "Temperature Offset"
        )
        self._update_attribute(self.attributes_by_name["max_present_value"].id, 5)
        self._update_attribute(self.attributes_by_name["min_present_value"].id, -5)
        self._update_attribute(self.attributes_by_name["resolution"].id, 0.1)
        self._update_attribute(self.attributes_by_name["application_type"].id, 0x0009)
        self._update_attribute(self.attributes_by_name["engineering_units"].id, 62)

    def set_value(self, value):
        """Set new temperature offset value."""
        self._update_attribute(self.attributes_by_name["present_value"].id, value)

    def get_value(self):
        """Get current temperature offset value."""
        return self._attr_cache.get(self.attributes_by_name["present_value"].id)

    async def write_attributes(self, attributes, manufacturer=None, **kwargs):
        """Modify value before passing it to the set_data tuya command."""
        for attrid, value in attributes.items():
            if isinstance(attrid, str):
                attrid = self.attributes_by_name[attrid].id
            if attrid not in self.attributes:
                self.error("%d is not a valid attribute id", attrid)
                continue
            self._update_attribute(attrid, value)

            await self.endpoint.tuya_manufacturer.write_attributes(
                {ZONNSMART_TEMPERATURE_CALIBRATION_ATTR: value * 10}, manufacturer=None
            )
        return ([foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)],)


class ZONNSMARTWindowOpenedTemp(LocalDataCluster, AnalogOutput):
    """AnalogOutput cluster for temperature when opened window detected."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.window_temperature_bus.add_listener(self)
        self._update_attribute(
            self.attributes_by_name["description"].id, "Opened Window Temperature"
        )
        self._update_attribute(
            self.attributes_by_name["max_present_value"].id,
            ZONNSMART_MAX_TEMPERATURE_VAL / 100,
        )
        self._update_attribute(
            self.attributes_by_name["min_present_value"].id,
            ZONNSMART_MIN_TEMPERATURE_VAL / 100,
        )
        self._update_attribute(self.attributes_by_name["resolution"].id, 0.5)
        self._update_attribute(self.attributes_by_name["application_type"].id, 0 << 16)
        self._update_attribute(self.attributes_by_name["engineering_units"].id, 62)

    def set_value(self, value):
        """Set temperature value when opened window detected."""
        self._update_attribute(self.attributes_by_name["present_value"].id, value / 10)

    def get_value(self):
        """Get temperature value when opened window detected."""
        return self._attr_cache.get(self.attributes_by_name["present_value"].id)

    async def write_attributes(self, attributes, manufacturer=None):
        """Modify value before passing it to the set_data tuya command."""
        for attrid, value in attributes.items():
            if isinstance(attrid, str):
                attrid = self.attributes_by_name[attrid].id
            if attrid not in self.attributes:
                self.error("%d is not a valid attribute id", attrid)
                continue
            self._update_attribute(attrid, value)

            # different Endpoint for compatibility issue
            await ZonnsmartManuClusterSelf.endpoint.tuya_manufacturer.write_attributes(
                {ZONNSMART_OPENED_WINDOW_TEMP: value * 10}, manufacturer=None
            )
        return ([foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)],)


SASWELL_CHILD_LOCK_ATTR = 0x0128  # [0/1] on/off 296
SASWELL_ANTI_FREEZE_ATTR = 0x010A  # [0/1] on/off 266
SASWELL_WINDOW_DETECT_ATTR = 0x0108  # [0/1] on/off 264
SASWELL_LIMESCALE_PROTECT_ATTR = 0x0182  # [0/1] on/off 386
SASWELL_TEMP_CORRECTION_ATTR = 0x021B  # uint32 - temp correction 539
SASWELL_ROOM_TEMP_ATTR = 0x0266  # uint32 - current room temp 614
SASWELL_AWAY_MODE_ATTR = 0x016A  # [0/1] on/off 362
SASWELL_SCHEDULE_MODE_ATTR = 0x016C  # [0/1] on/off 364
SASWELL_ONOFF_ATTR = 0x0165  # [0/1] on/off 357
SASWELL_TARGET_TEMP_ATTR = 0x0267  # uint32 - target temp 615
SASWELL_BATTERY_ALARM_ATTR = 0x569  # [0/1] on/off - battery low 1385

# Global
SaswellManufClusterSelf = {}


class SaswellManufCluster(TuyaManufClusterAttributes):
    """Manufacturer specific cluster for Saswell converting attributes <-> commands."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        SaswellManufClusterSelf[self.endpoint.device.ieee] = self

    server_commands = {
        0x0000: foundation.ZCLCommandDef(
            "set_data",
            {"param": TuyaManufCluster.Command},
            False,
            is_manufacturer_specific=False,
        ),
        0x0010: foundation.ZCLCommandDef(
            "mcu_version_req",
            {"param": t.uint16_t},
            False,
            is_manufacturer_specific=True,
        ),
        0x0024: foundation.ZCLCommandDef(
            "set_time",
            {"param": TuyaTimePayload},
            False,
            is_manufacturer_specific=True,
        ),
    }

    attributes = TuyaManufClusterAttributes.attributes.copy()
    attributes.update(
        {
            SASWELL_ONOFF_ATTR: ("on_off", t.uint8_t, True),
            SASWELL_TARGET_TEMP_ATTR: ("target_temperature", t.uint32_t, True),
            SASWELL_ROOM_TEMP_ATTR: ("current_room_temp", t.uint32_t, True),
            SASWELL_CHILD_LOCK_ATTR: ("child_lock", t.uint8_t, True),
            SASWELL_SCHEDULE_MODE_ATTR: ("schedule_mode", t.uint8_t, True),
            SASWELL_WINDOW_DETECT_ATTR: ("window_detection", t.uint8_t, True),
            SASWELL_ANTI_FREEZE_ATTR: ("anti_freeze_protection", t.uint8_t, True),
            SASWELL_LIMESCALE_PROTECT_ATTR: ("limescale_protection", t.uint8_t, True),
            SASWELL_AWAY_MODE_ATTR: ("away_mode", t.uint8_t, True),
            SASWELL_BATTERY_ALARM_ATTR: ("battery_low", t.uint8_t, True),
            SASWELL_TEMP_CORRECTION_ATTR: (
                "room_temperature_correction",
                t.int32s,
                True,
            ),
        }
    )

    DIRECT_MAPPED_ATTRS = {
        SASWELL_ROOM_TEMP_ATTR: ("local_temperature", lambda value: value * 10),
        SASWELL_TARGET_TEMP_ATTR: (
            "occupied_heating_setpoint",
            lambda value: value * 10,
        ),
        SASWELL_TEMP_CORRECTION_ATTR: (
            "local_temperature_calibration",
            lambda value: round(value * 10),
        ),
    }

    def _update_attribute(self, attrid, value):
        super()._update_attribute(attrid, value)
        if attrid in self.DIRECT_MAPPED_ATTRS:
            self.endpoint.device.thermostat_bus.listener_event(
                "temperature_change",
                self.DIRECT_MAPPED_ATTRS[attrid][0],
                value
                if self.DIRECT_MAPPED_ATTRS[attrid][1] is None
                else self.DIRECT_MAPPED_ATTRS[attrid][1](value),
            )
            #  room_temp_attr, target_temp_attr are processed further, see below

        if attrid == SASWELL_ONOFF_ATTR:
            self.endpoint.device.thermostat_bus.listener_event("on_off_event", value)
        elif attrid == SASWELL_SCHEDULE_MODE_ATTR:
            self.endpoint.device.thermostat_onoff_bus.listener_event(
                "schedule_mode_change", value
            )
        elif attrid == SASWELL_AWAY_MODE_ATTR:
            self.endpoint.device.thermostat_onoff_bus.listener_event(
                "away_mode_change", value
            )
        elif attrid == SASWELL_CHILD_LOCK_ATTR:
            self.endpoint.device.ui_bus.listener_event("child_lock_change", value)
            self.endpoint.device.thermostat_onoff_bus.listener_event(
                "child_lock_change", value
            )
        elif attrid == SASWELL_WINDOW_DETECT_ATTR:
            self.endpoint.device.thermostat_onoff_bus.listener_event(
                "window_detect_change", value
            )
        elif attrid == SASWELL_ANTI_FREEZE_ATTR:
            self.endpoint.device.thermostat_onoff_bus.listener_event(
                "anti_freeze_change", value
            )
        elif attrid == SASWELL_LIMESCALE_PROTECT_ATTR:
            self.endpoint.device.thermostat_onoff_bus.listener_event(
                "limescale_protection_change", value
            )
        elif attrid == SASWELL_BATTERY_ALARM_ATTR:
            self.endpoint.device.battery_bus.listener_event(
                "battery_alarm_event", value
            )
        elif attrid == SASWELL_TEMP_CORRECTION_ATTR:
            self.endpoint.device.temperature_calibration_bus.listener_event(
                "set_value", value
            )
        elif attrid in (SASWELL_ROOM_TEMP_ATTR, SASWELL_TARGET_TEMP_ATTR):
            self.endpoint.device.thermostat_bus.listener_event(
                "hass_climate_state_change", attrid, value
            )


class SaswellThermostat(TuyaThermostatCluster):
    """Thermostat cluster."""

    DIRECT_MAPPING_ATTRS = {
        "local_temperature_calibration": (
            SASWELL_TEMP_CORRECTION_ATTR,
            lambda value: round(value / 10),
        ),
        "occupied_heating_setpoint": (
            SASWELL_TARGET_TEMP_ATTR,
            lambda value: round(value / 10),
        ),
    }

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.thermostat_bus.listener_event(
            "temperature_change",
            "min_heat_setpoint_limit",
            500,
        )
        self.endpoint.device.thermostat_bus.listener_event(
            "temperature_change",
            "max_heat_setpoint_limit",
            3000,
        )

    def map_attribute(self, attribute, value):
        """Map standardized attribute value to dict of manufacturer values."""

        if attribute in self.DIRECT_MAPPING_ATTRS:
            return {
                self.DIRECT_MAPPING_ATTRS[attribute][0]: value
                if self.DIRECT_MAPPING_ATTRS[attribute][1] is None
                else self.DIRECT_MAPPING_ATTRS[attribute][1](value)
            }

        if attribute == "system_mode":
            if value == self.SystemMode.Off:
                return {SASWELL_ONOFF_ATTR: 0}
            if value == self.SystemMode.Heat:
                return {SASWELL_ONOFF_ATTR: 1}

    def on_off_event(self, value):
        """Handle on/off event."""
        if value == 1:
            self._update_attribute(
                self.attributes_by_name["system_mode"].id, Thermostat.SystemMode.Heat
            )
            self._update_attribute(
                self.attributes_by_name["running_mode"].id, Thermostat.RunningMode.Heat
            )
            self._update_attribute(
                self.attributes_by_name["running_state"].id,
                Thermostat.RunningState.Heat_State_On,
            )
            _LOGGER.debug("reported system_mode: heat")
        else:
            self._update_attribute(
                self.attributes_by_name["system_mode"].id, Thermostat.SystemMode.Off
            )
            self._update_attribute(
                self.attributes_by_name["running_mode"].id, Thermostat.RunningMode.Off
            )
            self._update_attribute(
                self.attributes_by_name["running_state"].id,
                Thermostat.RunningState.Idle,
            )
            _LOGGER.debug("reported system_mode: off")
        _LOGGER.debug("on/off event with value %d", value)

    def hass_climate_state_change(self, attrid, value):
        """Update of the HASS Climate gui state according to temp difference."""

        #  not in heat mode
        if (
            self._attr_cache.get(self.attributes_by_name["system_mode"].id)
            != Thermostat.SystemMode.Heat
        ):
            self.endpoint.device.thermostat_bus.listener_event("state_change", 0)
            return

        # heat mode:
        if attrid == SASWELL_ROOM_TEMP_ATTR:
            temp_current = value * 10
            temp_set = self._attr_cache.get(
                self.attributes_by_name["occupied_heating_setpoint"].id
            )
        else:  # SASWELL_TARGET_TEMP_ATTR
            temp_set = value * 10
            temp_current = self._attr_cache.get(
                self.attributes_by_name["local_temperature"].id
            )
        state = 0 if (int(temp_current) >= int(temp_set + 2)) else 1
        self.endpoint.device.thermostat_bus.listener_event("state_change", state)


class SaswellTemperatureOffset(LocalDataCluster, AnalogOutput):
    """AnalogOutput cluster for setting temperature offset."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.temperature_calibration_bus.add_listener(self)
        self._update_attribute(
            self.attributes_by_name["description"].id, "Temperature Offset"
        )
        self._update_attribute(self.attributes_by_name["max_present_value"].id, 6)
        self._update_attribute(self.attributes_by_name["min_present_value"].id, -6)
        self._update_attribute(self.attributes_by_name["resolution"].id, 1)
        self._update_attribute(self.attributes_by_name["application_type"].id, 13 << 16)
        self._update_attribute(self.attributes_by_name["engineering_units"].id, 62)

    def set_value(self, value):
        """Set new temperature offset value."""
        self._update_attribute(self.attributes_by_name["present_value"].id, value)

    def get_value(self):
        """Get current temperature offset value."""
        return self._attr_cache.get(self.attributes_by_name["present_value"].id)

    async def write_attributes(self, attributes, manufacturer=None, **kwargs):
        """Modify value before passing it to the set_data tuya command."""
        for attrid, value in attributes.items():
            if isinstance(attrid, str):
                attrid = self.attributes_by_name[attrid].id
            if attrid not in self.attributes:
                self.error("%d is not a valid attribute id", attrid)
                continue
            self._update_attribute(attrid, value)

            await SaswellManufClusterSelf[
                self.endpoint.device.ieee
            ].endpoint.tuya_manufacturer.write_attributes(
                {SASWELL_TEMP_CORRECTION_ATTR: value},
                manufacturer=None,
            )
        return ([foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)],)


class SaswellCustomOnOff(LocalDataCluster, OnOff):
    """Attributes and commands for switching devices between ‘On’ and ‘Off’ states."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.thermostat_onoff_bus.add_listener(self)

    # pylint: disable=R0201
    def map_attribute(self, attribute, value):
        """Map standardized attribute value to dict of manufacturer values."""
        return {}

    async def write_attributes(self, attributes, manufacturer=None, **kwargs):
        """Implement writeable attributes."""

        records = self._write_attr_records(attributes)

        if not records:
            return [[foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)]]

        manufacturer_attrs = {}
        for record in records:
            attr_name = self.attributes[record.attrid].name
            new_attrs = self.map_attribute(attr_name, record.value.value)

            _LOGGER.debug(
                "[0x%04x:%s:0x%04x] Mapping standard %s (0x%04x) "
                "with value %s to custom %s",
                self.endpoint.device.nwk,
                self.endpoint.endpoint_id,
                self.cluster_id,
                attr_name,
                record.attrid,
                repr(record.value.value),
                repr(new_attrs),
            )

            manufacturer_attrs.update(new_attrs)

        if not manufacturer_attrs:
            return [
                [
                    foundation.WriteAttributesStatusRecord(
                        foundation.Status.FAILURE, r.attrid
                    )
                    for r in records
                ]
            ]

        await SaswellManufClusterSelf[
            self.endpoint.device.ieee
        ].endpoint.tuya_manufacturer.write_attributes(
            manufacturer_attrs, manufacturer=manufacturer
        )

        return [[foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)]]

    async def command(
        self,
        command_id: Union[foundation.GeneralCommand, int, t.uint8_t],
        *args,
        manufacturer: Optional[Union[int, t.uint16_t]] = None,
        expect_reply: bool = True,
        tsn: Optional[Union[int, t.uint8_t]] = None,
    ):
        """Override the default Cluster command."""

        if command_id in (0x0000, 0x0001, 0x0002):
            if command_id == 0x0000:
                value = False
            elif command_id == 0x0001:
                value = True
            else:
                attrid = self.attributes_by_name["on_off"].id
                success, _ = await self.read_attributes(
                    (attrid,), manufacturer=manufacturer
                )
                try:
                    value = success[attrid]
                except KeyError:
                    return foundation.Status.FAILURE
                value = not value

            (res,) = await self.write_attributes(
                {"on_off": value},
                manufacturer=manufacturer,
            )
            return [command_id, res[0].status]

        return [command_id, foundation.Status.UNSUP_CLUSTER_COMMAND]


class SaswellChildLock(SaswellCustomOnOff):
    """Child Lock setting support. Please remember that CL has to be set manually on the device. This only controls if locking is possible at all."""

    def child_lock_change(self, value):
        """Update child lock attribute."""
        self._update_attribute(self.attributes_by_name["on_off"].id, value)

    def map_attribute(self, attribute, value):
        """Map child lock attribute."""
        if attribute == "on_off":
            return {SASWELL_CHILD_LOCK_ATTR: value}


class SaswellOpenWindowDetection(SaswellCustomOnOff):
    """Open Window Detection support."""

    def window_detect_change(self, value):
        """Update window detection attribute."""
        self._update_attribute(self.attributes_by_name["on_off"].id, value)

    def map_attribute(self, attribute, value):
        """Map window detection attribute."""
        if attribute == "on_off":
            return {SASWELL_WINDOW_DETECT_ATTR: value}


class SaswellAntiFreeze(SaswellCustomOnOff):
    """Anti-Freeze support."""

    def anti_freeze_change(self, value):
        """Update anti-freeze attribute."""
        self._update_attribute(self.attributes_by_name["on_off"].id, value)

    def map_attribute(self, attribute, value):
        """Map anti-freeze attribute."""
        if attribute == "on_off":
            return {SASWELL_ANTI_FREEZE_ATTR: value}


class SaswellLimescaleProtection(SaswellCustomOnOff):
    """Limescale Protection support."""

    def limescale_protection_change(self, value):
        """Update limescale protection attribute."""
        self._update_attribute(self.attributes_by_name["on_off"].id, value)

    def map_attribute(self, attribute, value):
        """Map limescale protection."""
        if attribute == "on_off":
            return {SASWELL_LIMESCALE_PROTECT_ATTR: value}


class SaswellScheduleMode(SaswellCustomOnOff):
    """Schedule Mode On/Off support."""

    def schedule_mode_change(self, value):
        """Map schedule mode."""
        self._update_attribute(self.attributes_by_name["on_off"].id, value)

    def map_attribute(self, attribute, value):
        """Map schedule mode."""
        if attribute == "on_off":
            return {SASWELL_SCHEDULE_MODE_ATTR: value}


class SaswellAwayMode(SaswellCustomOnOff):
    """Away Mode On/Off support."""

    def away_mode_change(self, value):
        """Update away mode attribute."""
        self._update_attribute(self.attributes_by_name["on_off"].id, value)

    def map_attribute(self, attribute, value):
        """On/off for away mode."""
        if attribute == "on_off":
            return {SASWELL_AWAY_MODE_ATTR: value}


class SaswellUserInterface(TuyaUserInterfaceCluster):
    """HVAC User interface cluster for tuya electric heating thermostats."""

    _CHILD_LOCK_ATTR = SASWELL_CHILD_LOCK_ATTR


class SaswellPowerConfigurationCluster(LocalDataCluster, PowerConfiguration):
    """Power configuration cluster."""

    def __init__(self, *args, **kwargs):
        """Init."""
        super().__init__(*args, **kwargs)
        self.endpoint.device.battery_bus.add_listener(self)

    def battery_alarm_event(self, value):
        """Handle reported battery state."""
        _LOGGER.debug("reported battery alert: %d", value)
        if value == 1:  # alert
            self._update_attribute(
                self.attributes_by_name["battery_percentage_remaining"].id, 0
            )  # report 0% battery
        else:
            self._update_attribute(
                self.attributes_by_name["battery_percentage_remaining"].id, 200
            )  # report 100% battery


class SiterwellGS361_Type1(TuyaThermostat):
    """SiterwellGS361 Thermostatic radiator valve and clones."""

    signature = {
        #  endpoint=1 profile=260 device_type=0 device_version=0 input_clusters=[0, 3]
        #  output_clusters=[3, 25]>
        MODELS_INFO: [
            ("_TYST11_jeaxp72v", "eaxp72v"),
            ("_TYST11_kfvq6avy", "fvq6avy"),
            ("_TYST11_zivfvd7h", "ivfvd7h"),
            ("_TYST11_hhrtiq0x", "hrtiq0x"),
            ("_TYST11_ps5v5jor", "s5v5jor"),
            ("_TYST11_owwdxjbx", "wwdxjbx"),
            ("_TYST11_8daqwrsj", "daqwrsj"),
            ("_TYST11_czk78ptr", "zk78ptr"),
        ],
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [Basic.cluster_id, Identify.cluster_id],
                OUTPUT_CLUSTERS: [Identify.cluster_id, Ota.cluster_id],
            }
        },
    }

    replacement = {
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.THERMOSTAT,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Identify.cluster_id,
                    SiterwellManufCluster,
                    SiterwellThermostat,
                    SiterwellUserInterface,
                    TuyaPowerConfigurationCluster2AA,
                ],
                OUTPUT_CLUSTERS: [Identify.cluster_id, Ota.cluster_id],
            }
        }
    }


class SiterwellGS361_Type2(TuyaThermostat):
    """SiterwellGS361 Thermostatic radiator valve and clones (2nd cluster signature)."""

    signature = {
        #  endpoint=1 profile=260 device_type=81 device_version=0 input_clusters=[0, 4, 5, 61184]
        #  output_clusters=[10, 25]>
        MODELS_INFO: [
            ("_TZE200_jeaxp72v", "TS0601"),
            ("_TZE200_kfvq6avy", "TS0601"),
            ("_TZE200_zivfvd7h", "TS0601"),
            ("_TZE200_hhrtiq0x", "TS0601"),
            ("_TZE200_ps5v5jor", "TS0601"),
            ("_TZE200_owwdxjbx", "TS0601"),
            ("_TZE200_8daqwrsj", "TS0601"),
            ("_TZE200_czk78ptr", "TS0601"),
            ("_TZE200_2cs6g9i7", "TS0601"),  # Brennenstuhl Zigbee Connect 01
            ("_TZE200_04yfvweb", "TS0601"),  # Appartme APRM-04-001
        ],
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.SMART_PLUG,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Groups.cluster_id,
                    Scenes.cluster_id,
                    TuyaManufClusterAttributes.cluster_id,
                ],
                OUTPUT_CLUSTERS: [Time.cluster_id, Ota.cluster_id],
            }
        },
    }

    replacement = {
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.THERMOSTAT,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Groups.cluster_id,
                    Scenes.cluster_id,
                    SiterwellManufCluster,
                    SiterwellThermostat,
                    SiterwellUserInterface,
                    TuyaPowerConfigurationCluster2AA,
                ],
                OUTPUT_CLUSTERS: [Time.cluster_id, Ota.cluster_id],
            }
        }
    }


class MoesHY368_Type1(TuyaThermostat):
    """MoesHY368 Thermostatic radiator valve."""

    def __init__(self, *args, **kwargs):
        """Init device."""
        self.window_detection_bus = Bus()
        super().__init__(*args, **kwargs)

    signature = {
        #  endpoint=1 profile=260 device_type=81 device_version=0 input_clusters=[0, 4, 5, 61184]
        #  output_clusters=[10, 25]>
        MODELS_INFO: [
            ("_TZE200_ckud7u2l", "TS0601"),
            ("_TZE200_ywdxldoj", "TS0601"),
            ("_TZE200_cwnjrr72", "TS0601"),
            ("_TZE200_2atgpdho", "TS0601"),
            ("_TZE200_pvvbommb", "TS0601"),
            ("_TZE200_4eeyebrt", "TS0601"),  # Immax NEO Smart (v1)
            ("_TZE200_rufdtfyv", "TS0601"),  # Immax NEO Smart (v2)
            ("_TZE200_cpmgn2cf", "TS0601"),
            ("_TZE200_9sfg7gm0", "TS0601"),
            ("_TZE200_8whxpsiw", "TS0601"),
            ("_TZE200_8thwkzxl", "TS0601"),  # Tervix Pro Line EVA 2
            ("_TZE200_xby0s3ta", "TS0601"),  # Sandy Beach HY367
        ],
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.SMART_PLUG,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Groups.cluster_id,
                    Scenes.cluster_id,
                    TuyaManufClusterAttributes.cluster_id,
                ],
                OUTPUT_CLUSTERS: [Time.cluster_id, Ota.cluster_id],
            }
        },
    }

    replacement = {
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.THERMOSTAT,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Groups.cluster_id,
                    Scenes.cluster_id,
                    MoesManufCluster,
                    MoesThermostat,
                    MoesUserInterface,
                    MoesWindowDetection,
                    TuyaPowerConfigurationCluster2AA,
                ],
                OUTPUT_CLUSTERS: [Time.cluster_id, Ota.cluster_id],
            }
        }
    }


# for Moes TRV _TZE200_b6wax7g0
class MoesHY368_Type1new(TuyaThermostat):
    """MoesHY368 Thermostatic radiator valve."""

    def __init__(self, *args, **kwargs):
        """Init device."""
        self.window_detection_bus = Bus()
        super().__init__(*args, **kwargs)

    signature = {
        #  endpoint=1 profile=260 device_type=81 device_version=0 input_clusters=[0, 4, 5, 61184]
        #  output_clusters=[10, 25]>
        MODELS_INFO: [
            ("_TZE200_b6wax7g0", "TS0601"),
        ],
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.SMART_PLUG,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Groups.cluster_id,
                    Scenes.cluster_id,
                    TuyaManufClusterAttributes.cluster_id,
                ],
                OUTPUT_CLUSTERS: [Time.cluster_id, Ota.cluster_id],
            }
        },
    }

    replacement = {
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.THERMOSTAT,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Groups.cluster_id,
                    Scenes.cluster_id,
                    MoesManufClusterNew,
                    MoesThermostatNew,
                    MoesUserInterface,
                    MoesWindowDetection,
                    TuyaPowerConfigurationCluster2AA,
                ],
                OUTPUT_CLUSTERS: [Time.cluster_id, Ota.cluster_id],
            }
        }
    }


class MoesHY368_Type2(TuyaThermostat):
    """MoesHY368 Thermostatic radiator valve (2nd cluster signature)."""

    def __init__(self, *args, **kwargs):
        """Init device."""
        self.window_detection_bus = Bus()
        super().__init__(*args, **kwargs)

    signature = {
        #  endpoint=1 profile=260 device_type=0 device_version=0 input_clusters=[0, 3]
        #  output_clusters=[3, 25]>
        MODELS_INFO: [
            ("_TYST11_ckud7u2l", "kud7u2l"),
            ("_TYST11_ywdxldoj", "wdxldoj"),
            ("_TYST11_cwnjrr72", "wnjrr72"),
            ("_TYST11_2atgpdho", "atgpdho"),
        ],
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [Basic.cluster_id, Identify.cluster_id],
                OUTPUT_CLUSTERS: [Identify.cluster_id, Ota.cluster_id],
            }
        },
    }

    replacement = {
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.THERMOSTAT,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Identify.cluster_id,
                    MoesManufCluster,
                    MoesThermostat,
                    MoesUserInterface,
                    MoesWindowDetection,
                    TuyaPowerConfigurationCluster2AA,
                ],
                OUTPUT_CLUSTERS: [Identify.cluster_id, Ota.cluster_id],
            }
        }
    }


class ZonnsmartTV01_ZG(TuyaThermostat):
    """ZONNSMART TV01-ZG Thermostatic radiator valve."""

    def __init__(self, *args, **kwargs):
        """Init device."""
        self.boost_bus = Bus()
        self.child_lock_bus = Bus()
        self.online_mode_bus = Bus()
        self.temperature_calibration_bus = Bus()
        self.window_detection_bus = Bus()
        self.window_temperature_bus = Bus()
        super().__init__(*args, **kwargs)

    signature = {
        #  endpoint=1 profile=260 device_type=81 device_version=0 input_clusters=[0, 4, 5, 61184]
        #  output_clusters=[10, 25]>
        MODELS_INFO: [
            ("_TZE200_7yoranx2", "TS0601"),  # MOES TV01 ZTRV-ZX-TV01-MS
            ("_TZE200_e9ba97vf", "TS0601"),  # Zonnsmart TV01-ZG
            ("_TZE200_hue3yfsn", "TS0601"),  # Zonnsmart TV02-ZG
            ("_TZE200_husqqvux", "TS0601"),  # Tesla Smart TSL-TRV-TV01ZG
            ("_TZE200_kly8gjlz", "TS0601"),  # EARU TV05-ZG
            ("_TZE200_lnbfnyxd", "TS0601"),  # Tesla Smart TSL-TRV-TV01ZG
            ("_TZE200_mudxchsu", "TS0601"),  # Foluu TV05
            ("_TZE200_kds0pmmv", "TS0601"),  # MOES TV02
            ("_TZE200_sur6q7ko", "TS0601"),  # LSC Smart Connect 3012732
            ("_TZE200_lllliz3p", "TS0601"),  # tuya TV02-Zigbee2
            ("_TZE200_fsow0qsk", "TS0601"),  # Tesla Smart TV500
        ],
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.SMART_PLUG,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Groups.cluster_id,
                    Scenes.cluster_id,
                    TuyaManufClusterAttributes.cluster_id,
                ],
                OUTPUT_CLUSTERS: [Time.cluster_id, Ota.cluster_id],
            }
        },
    }

    replacement = {
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.THERMOSTAT,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Groups.cluster_id,
                    Scenes.cluster_id,
                    ZONNSMARTBoost,
                    ZONNSMARTManufCluster,
                    ZONNSMARTTemperatureOffset,
                    ZONNSMARTThermostat,
                    ZONNSMARTUserInterface,
                    ZONNSMARTWindowDetection,
                    TuyaPowerConfigurationCluster2AA,
                ],
                OUTPUT_CLUSTERS: [Time.cluster_id, Ota.cluster_id],
            },
            2: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.COMBINED_INTERFACE,
                INPUT_CLUSTERS: [
                    ZONNSMARTChildLock,
                    ZONNSMARTWindowOpenedTemp,
                ],
                OUTPUT_CLUSTERS: [],
            },
            3: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.COMBINED_INTERFACE,
                INPUT_CLUSTERS: [
                    ZONNSMARTOnlineMode,
                ],
                OUTPUT_CLUSTERS: [],
            },
        }
    }


class Saswell_TYST11(TuyaThermostat):
    """Saswell Thermostatic Radiator Valve."""

    def __init__(self, *args, **kwargs):
        """Init device."""
        self.thermostat_onoff_bus = Bus()
        self.temperature_calibration_bus = Bus()
        super().__init__(*args, **kwargs)

    signature = {
        MODELS_INFO: [
            ("_TYST11_KGbxAXL2", "GbxAXL2"),
            ("_TYST11_c88teujp", "88teujp"),
            ("_TYST11_azqp6ssj", "zqp6ssj"),
            ("_TYST11_yw7cahqs", "w7cahqs"),
            ("_TYST11_9gvruqf5", "gvruqf5"),
            ("_TYST11_zuhszj9s", "uhszj9s"),
            ("_TYST11_caj4jz0i", "aj4jz0i"),
        ],
        ENDPOINTS: {
            # <SimpleDescriptor endpoint=1 profile=260 device_type=0
            # device_version=0
            # input_clusters=[0, 3]
            # output_clusters=[3, 25]>
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Identify.cluster_id,
                ],
                OUTPUT_CLUSTERS: [
                    Identify.cluster_id,
                    Ota.cluster_id,
                ],
            }
        },
    }
    replacement = {
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.THERMOSTAT,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    SaswellPowerConfigurationCluster,
                    Identify.cluster_id,
                    SaswellThermostat,
                    SaswellManufCluster,
                    SaswellOpenWindowDetection,
                    SaswellUserInterface,
                ],
                OUTPUT_CLUSTERS: [
                    Identify.cluster_id,
                    Ota.cluster_id,
                ],
            },
            2: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    SaswellChildLock,
                ],
                OUTPUT_CLUSTERS: [],
            },
            3: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    SaswellAntiFreeze,
                ],
                OUTPUT_CLUSTERS: [],
            },
            4: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    SaswellLimescaleProtection,
                ],
                OUTPUT_CLUSTERS: [],
            },
            5: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    SaswellScheduleMode,
                ],
                OUTPUT_CLUSTERS: [],
            },
            6: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    SaswellAwayMode,
                ],
                OUTPUT_CLUSTERS: [],
            },
            7: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.CONSUMPTION_AWARENESS_DEVICE,
                INPUT_CLUSTERS: [
                    SaswellTemperatureOffset,
                ],
                OUTPUT_CLUSTERS: [],
            },
        }
    }


class Saswell_TZE200(TuyaThermostat):
    """Saswell Thermostatic Radiator Valve."""

    def __init__(self, *args, **kwargs):
        """Init device."""
        self.thermostat_onoff_bus = Bus()
        self.temperature_calibration_bus = Bus()
        super().__init__(*args, **kwargs)

    signature = {
        MODELS_INFO: [
            ("_TZE200_c88teujp", "TS0601"),
            ("_TZE200_azqp6ssj", "TS0601"),
            ("_TZE200_yw7cahqs", "TS0601"),
            ("_TZE200_9gvruqf5", "TS0601"),
            ("_TZE200_zuhszj9s", "TS0601"),
            ("_TZE200_zr9c0day", "TS0601"),
            ("_TZE200_0dvm9mva", "TS0601"),
            ("_TZE200_h4cgnbzg", "TS0601"),
            ("_TZE200_exfrnlow", "TS0601"),
            ("_TZE200_9m4kmbfu", "TS0601"),
            ("_TZE200_3yp57tby", "TS0601"),
            ("_TZE200_mz5y07w2", "TS0601"),
        ],
        ENDPOINTS: {
            #  <SimpleDescriptor endpoint=1 profile=260 device_type=81
            #  device_version=1
            #  input_clusters=[0, 4, 5, 61184]
            #  output_clusters=[10, 25]>
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.SMART_PLUG,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    Groups.cluster_id,
                    Scenes.cluster_id,
                    TuyaManufClusterAttributes.cluster_id,
                ],
                OUTPUT_CLUSTERS: [Time.cluster_id, Ota.cluster_id],
            }
        },
    }

    replacement = {
        ENDPOINTS: {
            1: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.THERMOSTAT,
                INPUT_CLUSTERS: [
                    Basic.cluster_id,
                    SaswellPowerConfigurationCluster,
                    Groups.cluster_id,
                    Scenes.cluster_id,
                    SaswellThermostat,
                    SaswellManufCluster,
                    SaswellOpenWindowDetection,
                    SaswellUserInterface,
                ],
                OUTPUT_CLUSTERS: [Time.cluster_id, Ota.cluster_id],
            },
            2: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    SaswellChildLock,
                ],
                OUTPUT_CLUSTERS: [],
            },
            3: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    SaswellAntiFreeze,
                ],
                OUTPUT_CLUSTERS: [],
            },
            4: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    SaswellLimescaleProtection,
                ],
                OUTPUT_CLUSTERS: [],
            },
            5: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    SaswellScheduleMode,
                ],
                OUTPUT_CLUSTERS: [],
            },
            6: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                INPUT_CLUSTERS: [
                    SaswellAwayMode,
                ],
                OUTPUT_CLUSTERS: [],
            },
            7: {
                PROFILE_ID: zha.PROFILE_ID,
                DEVICE_TYPE: zha.DeviceType.CONSUMPTION_AWARENESS_DEVICE,
                INPUT_CLUSTERS: [
                    SaswellTemperatureOffset,
                ],
                OUTPUT_CLUSTERS: [],
            },
        }
    }


(
    TuyaQuirkBuilder("_TZE200_bvu2wnxz", "TS0601")
    .applies_to("_TZE200_6rdj8dzm", "TS0601")
    .applies_to("_TZE200_9xfjixap", "TS0601")
    .applies_to("_TZE200_p3dbf6qs", "TS0601")
    .applies_to("_TZE200_rxntag7i", "TS0601")
    .applies_to("_TZE200_yqgbrdyo", "TS0601")
    .applies_to("_TZE284_p3dbf6qs", "TS0601")
    .applies_to("_TZE200_rxq4iti9", "TS0601")
    .applies_to("_TZE200_hvaxb2tc", "TS0601")
    .applies_to("_TZE284_o3x45p96", "TS0601")
    .applies_to("_TZE284_c6wv4xyo", "TS0601")
    .applies_to("_TZE204_o3x45p96", "TS0601")
    .applies_to("_TZE204_ogx8u5z6", "TS0601")
    .tuya_dp(
        dp_id=2,
        ep_attribute=TuyaThermostatV2.ep_attribute,
        attribute_name=TuyaThermostatV2.AttributeDefs.system_mode.name,
        converter=lambda x: {
            TuyaThermostatSystemMode.Auto: Thermostat.SystemMode.Auto,
            TuyaThermostatSystemMode.Heat: Thermostat.SystemMode.Heat,
            TuyaThermostatSystemMode.Off: Thermostat.SystemMode.Off,
        }[x],
        dp_converter=lambda x: {
            Thermostat.SystemMode.Auto: TuyaThermostatSystemMode.Auto,
            Thermostat.SystemMode.Heat: TuyaThermostatSystemMode.Heat,
            Thermostat.SystemMode.Off: TuyaThermostatSystemMode.Off,
        }[x],
    )
    .tuya_dp(
        dp_id=3,
        ep_attribute=TuyaThermostatV2.ep_attribute,
        attribute_name=TuyaThermostatV2.AttributeDefs.running_state.name,
        converter=lambda x: 0x01 if not x else 0x00,  # Heat, Idle
    )
    .tuya_dp(
        dp_id=4,
        ep_attribute=TuyaThermostatV2.ep_attribute,
        attribute_name=TuyaThermostatV2.AttributeDefs.occupied_heating_setpoint.name,
        converter=lambda x: x * 10,
        dp_converter=lambda x: x // 10,
    )
    .tuya_dp(
        dp_id=5,
        ep_attribute=TuyaThermostatV2.ep_attribute,
        attribute_name=TuyaThermostatV2.AttributeDefs.local_temperature.name,
        converter=lambda x: x * 10,
    )
    .tuya_dp(
        dp_id=47,
        ep_attribute=TuyaThermostatV2.ep_attribute,
        attribute_name=TuyaThermostatV2.AttributeDefs.local_temperature_calibration.name,
        converter=lambda x: x,
        dp_converter=lambda x: x + 0x100000000 if x < 0 else x,
    )
    .tuya_switch(
        dp_id=7,
        attribute_name="child_lock",
        translation_key="child_lock",
        fallback_name="Child lock",
    )
    .tuya_binary_sensor(
        dp_id=35,
        attribute_name="error_or_battery_low",
        translation_key="error_or_battery_low",
        fallback_name="Error or battery low",
    )
    .tuya_switch(
        dp_id=36,
        attribute_name="frost_protection",
        translation_key="frost_protection",
        fallback_name="Frost protection",
    )
    .tuya_switch(
        dp_id=39,
        attribute_name="scale_protection",
        translation_key="scale_protection",
        fallback_name="Scale protection",
    )
    .adds(TuyaThermostatV2)
    .skip_configuration()
    .add_to_registry()
)
