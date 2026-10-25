# Supported devices

## Hisense on ConnectLife

| ConnectLife type | Hardware | Status |
|---|---|---|
| 009-19901 | HACZ01 Wi-Fi zone controller with HAAZ01 zone board (up to 8 zones) | Primary target |
| 009, other feature codes | Hisense split and ducted air conditioners | Unit controls; no zones |

### HACZ01 property map

| Property | Meaning |
|---|---|
| `t_power` | Power, 0 or 1 |
| `t_work_mode` | 0 fan, 1 heat, 2 cool, 3 dry, 4 auto |
| `t_temp` | Setpoint, 16 to 32 °C in 0.5 steps |
| `f_temp_in` | Return air temperature |
| `t_fan_speed` | 0 auto, 5 low, 7 medium, 9 high; reads 1 while Quiet is on |
| `t_super` | Powerful |
| `t_fan_mute` | Quiet |
| `t_eco` | Energy saving |
| `t_sleep` | Sleep, 0 or 1 (the cloud also lists family variants 2 to 4, which the app does not use) |
| `t_purify` | Health |
| `t_8heat` | 8 °C heat |
| `t_temp_compensate` | Temperature offset, 0 to 7 positive, 9 to 15 for minus 7 to minus 1 |
| `aus_zoneN_power` | Zone N on or off (N is 1 to 8) |
| `aus_zoneN_opencontrol` | Zone N opening, 0 to 100; the dampers move in 5% steps |
| `f_votage` | Supply voltage |
| `f_e_*` | Fault flags |

Sources: the ConnectLife app (feature list and fan speeds), the cloud's property list and static data, live tests on an HACZ01, and community device dumps from the `connectlife` project.

### Behaviour seen on an HACZ01

- Changes reach the unit and come back as a push update within 2 to 5 seconds.
- Powerful raises the setpoint to 30 °C, sets the fan to auto and cancels Sleep. Turning it off restores the previous setpoint and fan speed.
- Quiet, or any setpoint change, switches Energy Saving off.
- Sleep turns the fan down to low.
- Sleep, Quiet and 8 °C heat are reported only after they first change, so they show as unknown until then.
- The cloud's property list describes zone openings as "0,1"; the real range is 0 to 100.
- The static data (`query_static_data`) describes the unit precisely: fan gears, Sleep gears, half degree steps, zones, and which features exist. Cyborg HVAC uses it to decide which entities to create.
- Zone names, zone groups and app schedules live on ConnectLife's app-only services, which Hisense's third-party API does not expose. Name zones during setup; groups and schedules are handled in Home Assistant.

### Local control

ConnectLife Wi-Fi modules talk only to the ConnectLife cloud and offer no local control interface, so Cyborg HVAC uses the cloud. The backend interface leaves room for a local transport if one becomes possible.
