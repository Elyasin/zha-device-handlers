"""Tests for Saswell Tuya TRV quirks."""

from datetime import UTC, datetime
from unittest import mock

import pytest
import zigpy.types as t
from zigpy.zcl import foundation
from zigpy.zcl.clusters.general import AnalogOutput, OnOff
from zigpy.zcl.clusters.hvac import (
    KeypadLockout,
    RunningMode,
    RunningState,
    SystemMode,
    Thermostat,
    UserInterface,
)

from tests.common import ClusterListener
from zhaquirks.tuya.ts0601_trv import (
    SASWELL_ANTI_FREEZE_ATTR,
    SASWELL_AWAY_MODE_ATTR,
    SASWELL_CHILD_LOCK_ATTR,
    SASWELL_LIMESCALE_PROTECT_ATTR,
    SASWELL_ONOFF_ATTR,
    SASWELL_ROOM_TEMP_ATTR,
    SASWELL_SCHEDULE_MODE_ATTR,
    SASWELL_TARGET_TEMP_ATTR,
    SASWELL_TEMP_CORRECTION_ATTR,
    SASWELL_WINDOW_DETECT_ATTR,
    Saswell_TYST11,
    Saswell_TZE200,
)


@pytest.mark.parametrize(
    "manufacturer,model",
    [
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
)
def test_tze200_signature(assert_signature_matches_quirk, manufacturer, model):
    """Test Tuya devices signatures are matched to their quirks."""

    #  <SimpleDescriptor endpoint=1 profile=260 device_type=81
    #  device_version=1
    #  input_clusters=[0, 4, 5, 61184]
    #  output_clusters=[10, 25]>
    signature = {
        "endpoints": {
            "1": {
                "profile_id": 0x0104,
                "device_type": "0x0051",
                "in_clusters": ["0x0000", "0x0004", "0x0005", "0xef00"],
                "out_clusters": ["0x000a", "0x0019"],
            }
        },
        "manufacturer": manufacturer,
        "model": model,
        "class": "Saswell_TZE200",
    }
    assert_signature_matches_quirk(Saswell_TZE200, signature)


@pytest.mark.parametrize(
    "manufacturer,model",
    [
        ("_TYST11_KGbxAXL2", "GbxAXL2"),
        ("_TYST11_c88teujp", "88teujp"),
        ("_TYST11_azqp6ssj", "zqp6ssj"),
        ("_TYST11_yw7cahqs", "w7cahqs"),
        ("_TYST11_9gvruqf5", "gvruqf5"),
        ("_TYST11_zuhszj9s", "uhszj9s"),
        ("_TYST11_caj4jz0i", "aj4jz0i"),
    ],
)
def test_tyst11_signature(assert_signature_matches_quirk, manufacturer, model):
    """Test Tuya devices signatures are matched to their quirks."""

    # <SimpleDescriptor endpoint=1 profile=260 device_type=0
    # device_version=0
    # input_clusters=[0, 3]
    # output_clusters=[3, 25]>
    signature = {
        "endpoints": {
            "1": {
                "profile_id": 0x0104,
                "device_type": "0",
                "in_clusters": [
                    "0x0000",
                    "0x0003",
                ],
                "out_clusters": ["0x0003", "0x0019"],
            }
        },
        "manufacturer": manufacturer,
        "model": model,
        "class": "Saswell_TYST11",
    }
    assert_signature_matches_quirk(Saswell_TYST11, signature)


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_map_attributes(zigpy_device_from_quirk, quirk):
    """Test map attributes."""

    device = zigpy_device_from_quirk(quirk)

    thermostat_cluster = device.endpoints[1].thermostat
    input_params = [
        ("local_temperature_calibration", 60),
        ("occupied_heating_setpoint", 200),
        ("system_mode", SystemMode.Off),
        ("system_mode", SystemMode.Heat),
    ]
    return_values = [
        (SASWELL_TEMP_CORRECTION_ATTR, 6),
        (SASWELL_TARGET_TEMP_ATTR, 20),
        (SASWELL_ONOFF_ATTR, 0),
        (SASWELL_ONOFF_ATTR, 1),
    ]
    for (attr_desc, value), (attr_id, ret_value) in zip(input_params, return_values):
        assert thermostat_cluster.map_attribute(attr_desc, value) == {
            attr_id: ret_value
        }

    str_on_off = "on_off"

    open_window_detection_cluster = device.endpoints[1].on_off
    assert open_window_detection_cluster.map_attribute(str_on_off, 1) == {
        SASWELL_WINDOW_DETECT_ATTR: 1
    }

    child_lock_cluster = device.endpoints[2].on_off
    assert child_lock_cluster.map_attribute(str_on_off, 1) == {
        SASWELL_CHILD_LOCK_ATTR: 1
    }

    anti_freeze_cluster = device.endpoints[3].on_off
    assert anti_freeze_cluster.map_attribute(str_on_off, 1) == {
        SASWELL_ANTI_FREEZE_ATTR: 1
    }

    limescale_protection_cluster = device.endpoints[4].on_off
    assert limescale_protection_cluster.map_attribute(str_on_off, 1) == {
        SASWELL_LIMESCALE_PROTECT_ATTR: 1
    }

    schedule_mode_cluster = device.endpoints[5].on_off
    assert schedule_mode_cluster.map_attribute(str_on_off, 1) == {
        SASWELL_SCHEDULE_MODE_ATTR: 1
    }

    away_mode_cluster = device.endpoints[6].on_off
    assert away_mode_cluster.map_attribute(str_on_off, 1) == {SASWELL_AWAY_MODE_ATTR: 1}


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_trv_target_and_room_temperature(zigpy_device_from_quirk, quirk):
    """Test target and room temperature changes when system is off or in heat mode."""

    device = zigpy_device_from_quirk(quirk)
    manuf_cluster = device.endpoints[1].tuya_manufacturer
    thermostat_cluster = device.endpoints[1].thermostat
    thermostat_cluster_listener = ClusterListener(thermostat_cluster)

    # system mode is set to off, so after setting values the running mode/state are set to off/idle
    thermostat_cluster._attr_cache[Thermostat.AttributeDefs.system_mode.id] = (
        Thermostat.SystemMode.Off
    )
    thermostat_cluster._attr_last_updated[Thermostat.AttributeDefs.system_mode.id] = (
        datetime.now(UTC)
    )

    temp_target_attr_id = SASWELL_TARGET_TEMP_ATTR  # maps to occupied heating setpoint
    room_temp_attr_id = SASWELL_ROOM_TEMP_ATTR  # maps to local temperature

    # target temp change leaves running mode/state at off/idle
    manuf_cluster.update_attribute(temp_target_attr_id, 15)
    assert len(thermostat_cluster_listener.attribute_updates) == 3
    assert (
        thermostat_cluster_listener.attribute_updates[0][0]
        == Thermostat.AttributeDefs.occupied_heating_setpoint.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[0][1] == 150
    )  # value is multiplied by 10
    assert (
        thermostat_cluster_listener.attribute_updates[1][0]
        == Thermostat.AttributeDefs.running_mode.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[1][1]
        == Thermostat.RunningMode.Off
    )
    assert (
        thermostat_cluster_listener.attribute_updates[2][0]
        == Thermostat.AttributeDefs.running_state.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[2][1]
        == Thermostat.RunningState.Idle
    )

    # room temp change leaves running mode/state at off/idle
    manuf_cluster.update_attribute(room_temp_attr_id, 20)
    assert len(thermostat_cluster_listener.attribute_updates) == 6
    assert (
        thermostat_cluster_listener.attribute_updates[3][0]
        == Thermostat.AttributeDefs.local_temperature.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[3][1] == 200
    )  # value is multiplied by 10
    assert (
        thermostat_cluster_listener.attribute_updates[4][0]
        == Thermostat.AttributeDefs.running_mode.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[4][1]
        == Thermostat.RunningMode.Off
    )
    assert (
        thermostat_cluster_listener.attribute_updates[5][0]
        == Thermostat.AttributeDefs.running_state.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[5][1]
        == Thermostat.RunningState.Idle
    )

    # system mode is set to heat, so target and room temperature influence the running mode/state
    thermostat_cluster._attr_cache[Thermostat.AttributeDefs.system_mode.id] = (
        Thermostat.SystemMode.Heat
    )
    thermostat_cluster._attr_last_updated[Thermostat.AttributeDefs.system_mode.id] = (
        datetime.now(UTC)
    )

    # target temp > room temp -> heating
    manuf_cluster.update_attribute(temp_target_attr_id, 25)
    assert len(thermostat_cluster_listener.attribute_updates) == 9
    assert (
        thermostat_cluster_listener.attribute_updates[6][0]
        == Thermostat.AttributeDefs.occupied_heating_setpoint.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[6][1] == 250
    )  # value is multiplied by 10
    assert (
        thermostat_cluster_listener.attribute_updates[7][0]
        == Thermostat.AttributeDefs.running_mode.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[7][1]
        == Thermostat.RunningMode.Heat
    )
    assert (
        thermostat_cluster_listener.attribute_updates[8][0]
        == Thermostat.AttributeDefs.running_state.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[8][1]
        == Thermostat.RunningState.Heat_State_On
    )

    # set target temp < room temp -> no heating
    manuf_cluster.update_attribute(room_temp_attr_id, 30)
    assert len(thermostat_cluster_listener.attribute_updates) == 12
    assert (
        thermostat_cluster_listener.attribute_updates[9][0]
        == Thermostat.AttributeDefs.local_temperature.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[9][1] == 300
    )  # value is multiplied by 10
    assert (
        thermostat_cluster_listener.attribute_updates[10][0]
        == Thermostat.AttributeDefs.running_mode.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[10][1]
        == Thermostat.RunningMode.Off
    )
    assert (
        thermostat_cluster_listener.attribute_updates[11][0]
        == Thermostat.AttributeDefs.running_state.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[11][1]
        == Thermostat.RunningState.Idle
    )


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_trv_thermostat_check_min_max_heat_setpoint_limits_initialization(
    zigpy_device_from_quirk, quirk
):
    """Test that the min/max heat setpoint limits are set. Devices display min/max heat setpoints of 5°C/30°C."""

    device = zigpy_device_from_quirk(quirk)
    thermostat_cluster = device.endpoints[1].thermostat
    assert (
        thermostat_cluster._attr_cache[
            Thermostat.AttributeDefs.min_heat_setpoint_limit.id
        ]
        == 500
    )
    assert (
        thermostat_cluster._attr_cache[
            Thermostat.AttributeDefs.max_heat_setpoint_limit.id
        ]
        == 3000
    )


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_trv_temperature_calibration(zigpy_device_from_quirk, quirk):
    """Test temperature calibration."""

    device = zigpy_device_from_quirk(quirk)
    manuf_cluster = device.endpoints[1].tuya_manufacturer
    thermostat_cluster = device.endpoints[1].thermostat
    thermostat_cluster_listener = ClusterListener(thermostat_cluster)
    temp_calibration_cluster = device.endpoints[7].analog_output
    temp_calibration_cluster_listener = ClusterListener(temp_calibration_cluster)
    temp_corr_attr_id = SASWELL_TEMP_CORRECTION_ATTR

    # values assigned to attributes at initialization
    assert (
        temp_calibration_cluster._attr_cache[AnalogOutput.AttributeDefs.description.id]
        == "Temperature Offset"
    )
    assert (
        temp_calibration_cluster._attr_cache[
            AnalogOutput.AttributeDefs.max_present_value.id
        ]
        == 6
    )
    assert (
        temp_calibration_cluster._attr_cache[
            AnalogOutput.AttributeDefs.min_present_value.id
        ]
        == -6
    )
    assert (
        temp_calibration_cluster._attr_cache[AnalogOutput.AttributeDefs.resolution.id]
        == 1
    )
    assert (
        temp_calibration_cluster._attr_cache[
            AnalogOutput.AttributeDefs.application_type.id
        ]
        == 13 << 16
    )
    assert (
        temp_calibration_cluster._attr_cache[
            AnalogOutput.AttributeDefs.engineering_units.id
        ]
        == 62
    )

    # saswell temp correction attr -> local temperature calibration
    manuf_cluster.update_attribute(temp_corr_attr_id, 2)
    assert len(thermostat_cluster_listener.attribute_updates) == 1
    assert (
        thermostat_cluster_listener.attribute_updates[0][0]
        == Thermostat.AttributeDefs.local_temperature_calibration.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[0][1] == 20
    )  # value is multiplied by 10 and rounded
    assert len(temp_calibration_cluster_listener.attribute_updates) == 1
    assert (
        temp_calibration_cluster_listener.attribute_updates[0][0]
        == AnalogOutput.AttributeDefs.present_value.id
    )
    assert (
        temp_calibration_cluster_listener.attribute_updates[0][1]
        == temp_calibration_cluster.get_value()
    )


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_on_off_event(zigpy_device_from_quirk, quirk):
    """Test on/off event (system mode/running mode/running state)."""

    device = zigpy_device_from_quirk(quirk)
    manuf_cluster = device.endpoints[1].tuya_manufacturer
    thermostat_cluster = device.endpoints[1].thermostat
    thermostat_cluster_listener = ClusterListener(thermostat_cluster)
    on_off_attr_id = SASWELL_ONOFF_ATTR

    # saswell device on -> system mode/running mode/running state are "heating"
    manuf_cluster.update_attribute(on_off_attr_id, 1)
    assert len(thermostat_cluster_listener.attribute_updates) == 3
    assert (
        thermostat_cluster_listener.attribute_updates[0][0]
        == Thermostat.AttributeDefs.system_mode.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[0][1]
        == Thermostat.SystemMode.Heat
    )
    assert (
        thermostat_cluster_listener.attribute_updates[1][0]
        == Thermostat.AttributeDefs.running_mode.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[1][1]
        == Thermostat.RunningMode.Heat
    )
    assert (
        thermostat_cluster_listener.attribute_updates[2][0]
        == Thermostat.AttributeDefs.running_state.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[2][1]
        == Thermostat.RunningState.Heat_State_On
    )

    # saswell off -> system mode/running mode/running state are off/idle
    manuf_cluster.update_attribute(on_off_attr_id, 0)
    assert len(thermostat_cluster_listener.attribute_updates) == 6
    assert (
        thermostat_cluster_listener.attribute_updates[3][0]
        == Thermostat.AttributeDefs.system_mode.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[3][1] == Thermostat.SystemMode.Off
    )
    assert (
        thermostat_cluster_listener.attribute_updates[4][0]
        == Thermostat.AttributeDefs.running_mode.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[4][1]
        == Thermostat.RunningMode.Off
    )
    assert (
        thermostat_cluster_listener.attribute_updates[5][0]
        == Thermostat.AttributeDefs.running_state.id
    )
    assert (
        thermostat_cluster_listener.attribute_updates[5][1]
        == Thermostat.RunningState.Idle
    )


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_schedule_mode(zigpy_device_from_quirk, quirk):
    """Test schedule mode."""

    device = zigpy_device_from_quirk(quirk)
    manuf_cluster = device.endpoints[1].tuya_manufacturer
    schedule_mode_cluster = device.endpoints[5].on_off
    schedule_mode_cluster_listener = ClusterListener(schedule_mode_cluster)
    schedule_mode_attr_id = SASWELL_SCHEDULE_MODE_ATTR

    # schedule mode activated
    manuf_cluster.update_attribute(schedule_mode_attr_id, True)
    assert len(schedule_mode_cluster_listener.attribute_updates) == 1
    assert (
        schedule_mode_cluster_listener.attribute_updates[0][0]
        == OnOff.AttributeDefs.on_off.id
    )
    assert schedule_mode_cluster_listener.attribute_updates[0][1] is True

    # schedule mode deactivated
    manuf_cluster.update_attribute(schedule_mode_attr_id, False)
    assert len(schedule_mode_cluster_listener.attribute_updates) == 2
    assert (
        schedule_mode_cluster_listener.attribute_updates[1][0]
        == OnOff.AttributeDefs.on_off.id
    )
    assert schedule_mode_cluster_listener.attribute_updates[1][1] is False


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_away_mode(zigpy_device_from_quirk, quirk):
    """Test away mode."""

    device = zigpy_device_from_quirk(quirk)
    manuf_cluster = device.endpoints[1].tuya_manufacturer
    away_mode_cluster = device.endpoints[6].on_off
    away_mode_cluster_listener = ClusterListener(away_mode_cluster)
    away_mode_attr_id = SASWELL_AWAY_MODE_ATTR

    # away mode activated
    manuf_cluster.update_attribute(away_mode_attr_id, True)
    assert len(away_mode_cluster_listener.attribute_updates) == 1
    assert (
        away_mode_cluster_listener.attribute_updates[0][0]
        == OnOff.AttributeDefs.on_off.id
    )
    assert away_mode_cluster_listener.attribute_updates[0][1] is True

    # away mode deactivated
    manuf_cluster.update_attribute(away_mode_attr_id, False)
    assert len(away_mode_cluster_listener.attribute_updates) == 2
    assert (
        away_mode_cluster_listener.attribute_updates[1][0]
        == OnOff.AttributeDefs.on_off.id
    )
    assert away_mode_cluster_listener.attribute_updates[1][1] is False


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_child_lock(zigpy_device_from_quirk, quirk):
    """Test child lock enabled/disabled."""

    device = zigpy_device_from_quirk(quirk)
    manuf_cluster = device.endpoints[1].tuya_manufacturer
    child_lock_cluster = device.endpoints[2].on_off
    child_lock_cluster_listener = ClusterListener(child_lock_cluster)
    ui_interface_cluster = device.endpoints[1].thermostat_ui
    ui_interface_cluster_listener = ClusterListener(ui_interface_cluster)
    child_lock_attr_id = SASWELL_CHILD_LOCK_ATTR

    # child lock enabled
    manuf_cluster.update_attribute(child_lock_attr_id, True)
    assert len(child_lock_cluster_listener.attribute_updates) == 1
    assert (
        child_lock_cluster_listener.attribute_updates[0][0]
        == OnOff.AttributeDefs.on_off.id
    )
    assert child_lock_cluster_listener.attribute_updates[0][1] is True
    assert len(ui_interface_cluster_listener.attribute_updates) == 1
    assert (
        ui_interface_cluster_listener.attribute_updates[0][0]
        == UserInterface.AttributeDefs.keypad_lockout.id
    )
    assert (
        ui_interface_cluster_listener.attribute_updates[0][1]
        == KeypadLockout.Level_1_lockout
    )

    # child lock disabled
    manuf_cluster.update_attribute(child_lock_attr_id, False)
    assert len(child_lock_cluster_listener.attribute_updates) == 2
    assert (
        child_lock_cluster_listener.attribute_updates[1][0]
        == OnOff.AttributeDefs.on_off.id
    )
    assert child_lock_cluster_listener.attribute_updates[1][1] is False
    assert len(ui_interface_cluster_listener.attribute_updates) == 2
    assert (
        ui_interface_cluster_listener.attribute_updates[1][0]
        == UserInterface.AttributeDefs.keypad_lockout.id
    )
    assert (
        ui_interface_cluster_listener.attribute_updates[1][1]
        == KeypadLockout.No_lockout
    )


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_open_window_detection(zigpy_device_from_quirk, quirk):
    """Test open window detection."""

    device = zigpy_device_from_quirk(quirk)
    manuf_cluster = device.endpoints[1].tuya_manufacturer
    window_open_detect_cluster = device.endpoints[1].on_off
    window_open_detect_cluster_listener = ClusterListener(window_open_detect_cluster)
    window_open_detect_attr_id = SASWELL_WINDOW_DETECT_ATTR

    # open window detection activated
    manuf_cluster.update_attribute(window_open_detect_attr_id, True)
    assert len(window_open_detect_cluster_listener.attribute_updates) == 1
    assert (
        window_open_detect_cluster_listener.attribute_updates[0][0]
        == OnOff.AttributeDefs.on_off.id
    )
    assert window_open_detect_cluster_listener.attribute_updates[0][1] is True

    # open window detection deactivated
    manuf_cluster.update_attribute(window_open_detect_attr_id, False)
    assert len(window_open_detect_cluster_listener.attribute_updates) == 2
    assert (
        window_open_detect_cluster_listener.attribute_updates[1][0]
        == OnOff.AttributeDefs.on_off.id
    )
    assert window_open_detect_cluster_listener.attribute_updates[1][1] is False


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_anti_freeze(zigpy_device_from_quirk, quirk):
    """Test anti freeze."""

    device = zigpy_device_from_quirk(quirk)
    manuf_cluster = device.endpoints[1].tuya_manufacturer
    anti_freeze_cluster = device.endpoints[3].on_off
    cluster_listener = ClusterListener(anti_freeze_cluster)
    anti_freeze_attr_id = SASWELL_ANTI_FREEZE_ATTR

    # anti freeze activated
    manuf_cluster.update_attribute(anti_freeze_attr_id, True)
    assert len(cluster_listener.attribute_updates) == 1
    assert cluster_listener.attribute_updates[0][0] == OnOff.AttributeDefs.on_off.id
    assert cluster_listener.attribute_updates[0][1] is True

    # anti freeze deactivated
    manuf_cluster.update_attribute(anti_freeze_attr_id, False)
    assert len(cluster_listener.attribute_updates) == 2
    assert cluster_listener.attribute_updates[1][0] == OnOff.AttributeDefs.on_off.id
    assert cluster_listener.attribute_updates[1][1] is False


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TYST11,
        Saswell_TZE200,
    ),
)
async def test_saswell_limescale_protection(zigpy_device_from_quirk, quirk):
    """Test limescale protection."""

    device = zigpy_device_from_quirk(quirk)
    manuf_cluster = device.endpoints[1].tuya_manufacturer
    limescale_protection_cluster = device.endpoints[4].on_off
    limescale_protection_cluster_listener = ClusterListener(
        limescale_protection_cluster
    )
    limescale_protection_attr_id = SASWELL_LIMESCALE_PROTECT_ATTR

    # anti freeze activated
    manuf_cluster.update_attribute(limescale_protection_attr_id, True)
    assert len(limescale_protection_cluster_listener.attribute_updates) == 1
    assert (
        limescale_protection_cluster_listener.attribute_updates[0][0]
        == OnOff.AttributeDefs.on_off.id
    )
    assert limescale_protection_cluster_listener.attribute_updates[0][1] is True

    # anti freeze deactivated
    manuf_cluster.update_attribute(limescale_protection_attr_id, False)
    assert len(limescale_protection_cluster_listener.attribute_updates) == 2
    assert (
        limescale_protection_cluster_listener.attribute_updates[1][0]
        == OnOff.AttributeDefs.on_off.id
    )
    assert limescale_protection_cluster_listener.attribute_updates[1][1] is False


ZCL_SASWELL_TUYA_ROOM_TEMP = b"\tp\x02\x00\x02\x66\x02\x00\x04\x00\x00\x00\xb3"
ZCL_SASWELL_TUYA_ON = b"\t2\x01\x03\x04\x65\x01\x00\x01\x01"
ZCL_SASWELL_TUYA_OFF = b"\t2\x01\x03\x04\x65\x01\x00\x01\x00"
ZCL_SASWELL_TUYA_TARGET_TEMP = b"\t3\x01\x03\x05\x67\x02\x00\x04\x00\x00\x002"
ZCL_SASWELL_TUYA_SCHEDULE_MODE = b"\t2\x01\x03\x04\x6c\x01\x00\x01\x01"
ZCL_SASWELL_TUYA_CHILD_LOCK = b"\t2\x01\x03\x04\x28\x01\x00\x01\x01"
ZCL_SASWELL_TUYA_ANTI_FREEZE = b"\t2\x01\x03\x04\x0a\x01\x00\x01\x01"
ZCL_SASWELL_TUYA_WINDOW_OPEN_DETECT_ATTR = b"\t2\x01\x03\x04\x08\x01\x00\x01\x01"
ZCL_SASWELL_TUYA_LIMESCALE_PROTECT = b"\t2\x01\x03\x04\x82\x01\x00\x01\x01"
ZCL_SASWELL_TUYA_TEMP_CORRECTION_ATTR = (
    b"\t3\x01\x03\x05\x1b\x02\x00\x04\x00\x00\x00\x06"
)
ZCL_SASWELL_TUYA_AWAY_MODE_ATTR = b"\t2\x01\x03\x04\x6a\x01\x00\x01\x01"
ZCL_SASWELL_TUYA_BATTERY_ALARM_ATTR = b"\t2\x01\x03\x04\x69\x05\x00\x01\x01"


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TZE200,
        Saswell_TYST11,
    ),
)
async def test_saswell_state_report(zigpy_device_from_quirk, quirk):
    """Test thermostatic valves standard reporting from incoming commands."""

    valve_dev = zigpy_device_from_quirk(quirk)
    tuya_manuf_cluster = valve_dev.endpoints[1].tuya_manufacturer

    thermostat_listener = ClusterListener(valve_dev.endpoints[1].thermostat)
    open_window_listener = ClusterListener(valve_dev.endpoints[1].on_off)
    battery_power_low_listener = ClusterListener(valve_dev.endpoints[1].power)
    child_lock_listener = ClusterListener(valve_dev.endpoints[2].on_off)
    anti_freeze_listener = ClusterListener(valve_dev.endpoints[3].on_off)
    limescale_protection_listener = ClusterListener(valve_dev.endpoints[4].on_off)
    schedule_mode_listener = ClusterListener(valve_dev.endpoints[5].on_off)
    away_mode_listener = ClusterListener(valve_dev.endpoints[6].on_off)
    ui_listener = ClusterListener(valve_dev.endpoints[1].thermostat_ui)
    temp_calibration_listener = ClusterListener(valve_dev.endpoints[7].analog_output)

    frames = (
        ZCL_SASWELL_TUYA_ROOM_TEMP,
        ZCL_SASWELL_TUYA_ON,
        ZCL_SASWELL_TUYA_OFF,
        ZCL_SASWELL_TUYA_TARGET_TEMP,
        ZCL_SASWELL_TUYA_WINDOW_OPEN_DETECT_ATTR,
        ZCL_SASWELL_TUYA_CHILD_LOCK,
        ZCL_SASWELL_TUYA_ANTI_FREEZE,
        ZCL_SASWELL_TUYA_LIMESCALE_PROTECT,
        ZCL_SASWELL_TUYA_SCHEDULE_MODE,
        ZCL_SASWELL_TUYA_AWAY_MODE_ATTR,
        ZCL_SASWELL_TUYA_TEMP_CORRECTION_ATTR,
        ZCL_SASWELL_TUYA_BATTERY_ALARM_ATTR,
    )
    for frame in frames:
        hdr, args = tuya_manuf_cluster.deserialize(frame)
        tuya_manuf_cluster.handle_message(hdr, args)

    assert len(thermostat_listener.cluster_commands) == 0
    assert len(thermostat_listener.attribute_updates) == 13
    # TEMP
    assert thermostat_listener.attribute_updates[0][0] == 0x0000
    assert thermostat_listener.attribute_updates[0][1] == 1790
    assert thermostat_listener.attribute_updates[1][0] == 0x1E
    assert thermostat_listener.attribute_updates[1][1] == RunningMode.Off
    assert thermostat_listener.attribute_updates[2][0] == 0x29
    assert thermostat_listener.attribute_updates[2][1] == RunningState.Idle
    # On
    assert thermostat_listener.attribute_updates[3][0] == 0x1C
    assert thermostat_listener.attribute_updates[3][1] == SystemMode.Heat
    assert thermostat_listener.attribute_updates[4][0] == 0x1E
    assert thermostat_listener.attribute_updates[4][1] == RunningMode.Heat
    assert thermostat_listener.attribute_updates[5][0] == 0x29
    assert thermostat_listener.attribute_updates[5][1] == RunningState.Heat_State_On
    # Off
    assert thermostat_listener.attribute_updates[6][0] == 0x1C
    assert thermostat_listener.attribute_updates[6][1] == SystemMode.Off
    assert thermostat_listener.attribute_updates[7][0] == 0x1E
    assert thermostat_listener.attribute_updates[7][1] == RunningMode.Off
    assert thermostat_listener.attribute_updates[8][0] == 0x29
    assert thermostat_listener.attribute_updates[8][1] == RunningState.Idle
    # Target temp
    assert thermostat_listener.attribute_updates[9][0] == 0x0012
    assert thermostat_listener.attribute_updates[9][1] == 500
    assert thermostat_listener.attribute_updates[10][0] == 0x001E
    assert thermostat_listener.attribute_updates[10][1] == RunningMode.Off
    assert thermostat_listener.attribute_updates[11][0] == 0x0029
    assert thermostat_listener.attribute_updates[11][1] == RunningState.Idle
    # Temp calibration
    assert thermostat_listener.attribute_updates[9][0] == 0x0012
    assert thermostat_listener.attribute_updates[9][1] == 500

    # Open Window Detection
    assert len(open_window_listener.cluster_commands) == 0
    assert len(open_window_listener.attribute_updates) == 1
    assert open_window_listener.attribute_updates[0][0] == 0x0000
    assert open_window_listener.attribute_updates[0][1] == 1

    # Child Lock
    assert len(child_lock_listener.cluster_commands) == 0
    assert len(child_lock_listener.attribute_updates) == 1
    assert child_lock_listener.attribute_updates[0][0] == 0x0000
    assert child_lock_listener.attribute_updates[0][1] == 1

    # Anti Freeze
    assert len(anti_freeze_listener.cluster_commands) == 0
    assert len(anti_freeze_listener.attribute_updates) == 1
    assert anti_freeze_listener.attribute_updates[0][0] == 0x0000
    assert anti_freeze_listener.attribute_updates[0][1] == 1

    # Limescale Protection
    assert len(limescale_protection_listener.cluster_commands) == 0
    assert len(limescale_protection_listener.attribute_updates) == 1
    assert limescale_protection_listener.attribute_updates[0][0] == 0x0000
    assert limescale_protection_listener.attribute_updates[0][1] == 1

    # Schedule Mode
    assert len(schedule_mode_listener.cluster_commands) == 0
    assert len(schedule_mode_listener.attribute_updates) == 1
    assert schedule_mode_listener.attribute_updates[0][0] == 0x0000
    assert schedule_mode_listener.attribute_updates[0][1] == 1

    # Away Mode
    assert len(away_mode_listener.cluster_commands) == 0
    assert len(away_mode_listener.attribute_updates) == 1
    assert away_mode_listener.attribute_updates[0][0] == 0x0000
    assert away_mode_listener.attribute_updates[0][1] == 1

    # Temp calibration
    assert len(temp_calibration_listener.cluster_commands) == 0
    assert len(temp_calibration_listener.attribute_updates) == 1
    assert temp_calibration_listener.attribute_updates[0][0] == 0x0055
    assert temp_calibration_listener.attribute_updates[0][1] == 6
    assert len(ui_listener.cluster_commands) == 0
    assert len(ui_listener.attribute_updates) == 1
    assert ui_listener.attribute_updates[0][0] == 0x0001
    assert ui_listener.attribute_updates[0][1] == KeypadLockout.Level_1_lockout

    # Battery power low
    assert len(battery_power_low_listener.cluster_commands) == 0
    assert len(battery_power_low_listener.attribute_updates) == 1
    assert battery_power_low_listener.attribute_updates[0][0] == 0x21
    assert battery_power_low_listener.attribute_updates[0][1] == 0


@pytest.mark.parametrize(
    "quirk",
    (
        Saswell_TZE200,
        Saswell_TYST11,
    ),
)
async def test_saswell_send_attribute(zigpy_device_from_quirk, quirk):
    """Test thermostatic valve outgoing commands."""

    valve_dev = zigpy_device_from_quirk(quirk)
    tuya_cluster = valve_dev.endpoints[1].tuya_manufacturer
    thermostat_cluster = valve_dev.endpoints[1].thermostat

    async def async_success(*args, **kwargs):
        return foundation.Status.SUCCESS

    with mock.patch.object(
        tuya_cluster.endpoint, "request", side_effect=async_success
    ) as m1:
        (status,) = await thermostat_cluster.write_attributes(
            {
                "occupied_heating_setpoint": 2500,
            }
        )
        m1.assert_called_with(
            cluster=0xEF00,
            sequence=1,
            data=b"\x01\x01\x00\x00\x01g\x02\x00\x04\x00\x00\x00\xfa",
            command_id=0,
            timeout=5,
            expect_reply=False,
            use_ieee=False,
            ask_for_ack=None,
            priority=t.PacketPriority.NORMAL,
        )
        assert status == [
            foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)
        ]

        (status,) = await thermostat_cluster.write_attributes(
            {
                "system_mode": 0x00,
            }
        )
        m1.assert_called_with(
            cluster=0xEF00,
            sequence=2,
            data=b"\x01\x02\x00\x00\x02e\x01\x00\x01\x00",
            command_id=0,
            timeout=5,
            expect_reply=False,
            use_ieee=False,
            ask_for_ack=None,
            priority=t.PacketPriority.NORMAL,
        )
        assert status == [
            foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)
        ]

        (status,) = await thermostat_cluster.write_attributes(
            {
                "system_mode": 0x04,
            }
        )
        m1.assert_called_with(
            cluster=0xEF00,
            sequence=3,
            data=b"\x01\x03\x00\x00\x03e\x01\x00\x01\x01",
            command_id=0,
            timeout=5,
            expect_reply=False,
            use_ieee=False,
            ask_for_ack=None,
            priority=t.PacketPriority.NORMAL,
        )
        assert status == [
            foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)
        ]

        (status,) = await thermostat_cluster.write_attributes(
            {
                "local_temperature_calibration": 6,
            }
        )
        m1.assert_called_with(
            cluster=0xEF00,
            sequence=4,
            data=b"\x01\x04\x00\x00\x04\x1b\x02\x00\x04\x00\x00\x00\x01",
            command_id=0,
            timeout=5,
            expect_reply=False,
            use_ieee=False,
            ask_for_ack=None,
            priority=t.PacketPriority.NORMAL,
        )
        assert status == [
            foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)
        ]

        # simulate a target temp update so that relative changes can work
        hdr, args = tuya_cluster.deserialize(ZCL_SASWELL_TUYA_TARGET_TEMP)
        tuya_cluster.handle_message(hdr, args)
        _, status = await thermostat_cluster.command(0x0000, 0x00, 20)
        m1.assert_called_with(
            cluster=0xEF00,
            sequence=5,
            data=b"\x01\x05\x00\x00\x05g\x02\x00\x04\x00\x00\x00F",
            command_id=0,
            timeout=5,
            expect_reply=False,
            use_ieee=False,
            ask_for_ack=None,
            priority=t.PacketPriority.NORMAL,
        )
        assert status == foundation.Status.SUCCESS

        _, status = await thermostat_cluster.command(0x0002)
        assert status == foundation.Status.UNSUP_CLUSTER_COMMAND
