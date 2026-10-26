# Card contract

The Cyborg HVAC card reads one websocket feed per unit instead of rebuilding the comfort logic in the browser.

## Commands

| Type | Fields | Result |
|---|---|---|
| `cyborg_hvac/list` | none | `[{entry_id, title, units: [{unit_id, name, device_id}]}]` |
| `cyborg_hvac/subscribe` | `entry_id`, `unit_id` (both optional; the first unit is used when left out) | Success, then an event with the snapshot below on every change |

The subscription survives the integration reloading (for example after a zone is edited).

## Snapshot (`schema: 1`)

```json
{
  "schema": 1,
  "entry_id": "…",
  "unit": {
    "id": "…", "name": "Duct Air Conditioner", "device_id": "…", "climate": "climate.duct_air_conditioner",
    "entities": {"advice": "sensor.duct_air_conditioner_advice", "guest_mode": "switch.duct_air_conditioner_guest_mode",
                 "sleep_mode": "switch.…", "sleep_target": "number.duct_air_conditioner_sleep_temperature",
    "features_entities": {"powerful": "switch.duct_air_conditioner_powerful", "eco": "switch.…", "health": "switch.…"},
                 "presence_zoning": "switch.…", "room_balancing": "switch.…", "weather_precondition": "switch.…", "solar_shift": "switch.…"},
    "online": true, "power": true, "mode": "heat", "target": 22.0, "current": 21.0, "fan": "auto",
    "features": {"powerful": false, "eco": true, "quiet": false},
    "caps": {"modes": ["fan_only", "heat", "cool", "dry", "auto"], "fans": ["auto", "silent", "low", "medium", "high"],
             "min": 16, "max": 32, "step": 0.5, "features": ["powerful", "eco"], "zone_step": 5}
  },
  "zones": [
    {"zone": 1, "name": "MASTER", "device_id": "…", "on": true, "opening": 100,
     "temperature": 21.4, "humidity": 52, "occupied": true, "window_open": false, "door_open": false, "guest": false, "sleep": true,
     "band": {"low": 20.0, "high": 24.0}, "demand": 0, "weight": 1.0, "priority": "bedroom",
     "role": "hold",
     "health": {"dew_point": 9.8, "surface_humidity": 58, "damp_minutes": 0, "mould": false,
                "condensation": false, "overheated": false, "too_cold": false, "floor": 16.0, "ceiling": 28.0},
     "entities": {"damper": "cover.master", "power": "switch.master_power"}}
  ],
  "house": {"period": "day", "band": {"low": 20.0, "high": 24.0}, "indoor": 21.3, "indoor_basis": "rooms",
            "deciding_zones": [1, 3, 4], "outdoor": 14.5,
            "anyone_home": true, "demand": 12, "solar_export_w": 2400,
            "season": "heating", "heatwave": false, "peak": false, "cold_snap": false,
            "hottest": 18.0, "hottest_at": "2026-10-25T15:00:00+11:00", "coldest": 9.0,
            "guest_mode": false, "sleep_mode": false, "sleep_target": 23.0},
  "forecast": [{"time": "2026-10-25T15:00:00+11:00", "temperature": 18.0, "condition": "sunny"}],
  "advice": [{"id": "mode_heat", "category": "mode", "severity": "suggest", "title": "Heating recommended",
              "reason": "Rooms are below 20 °C. Heat to 21 °C.", "zone": null,
              "action": {"power": true, "mode": "heat", "target_temp": 21.0}}],
  "autopilot": {"behaviours": {"presence_zoning": false, "room_balancing": true,
                                "weather_precondition": true, "solar_shift": false},
                "paused_until": null, "zones_paused": {"2": "2026-10-25T16:02:00+00:00"},
                "fan_paused_until": null,
                "last_action": {"time": "…", "action": {"zones": {"1": {"opening": 60}}},
                                "reason": "Heating HQ (19.5 °C). Letting MASTER (24 °C) cool on its own (8 °C outside)."},
                "decision": {"mode": "heat", "reason": "…", "roles": {"1": "shed", "2": "serve", "3": "keeper"}}},
  "plan": {"blocks": [{"kind": "preheat", "start": "…", "end": "…", "mode": "heat", "target": 18.5,
                       "reason": "Rooms would be 16.8 °C by night. Warming from 20:40 has them at 18.5 °C in time."}],
           "periods": [{"start": "…", "end": "…", "period": "day", "band": {"low": 20.0, "high": 24.0}}],
           "model": {"1": {"loss": 0.0015, "gain": 0.15, "confident": false}}},
  "forecast_hours": 48,
  "updated": "2026-10-25T14:02:11+11:00"
}
```

- `demand` runs from 100 (needs full heating) to the same figure in the cooling direction, sent as a negative number. The card shows it as words or colour, never with a minus sign.
- `occupied` is `null` when a zone has no presence sensor.
- `house.indoor` is the weighted average of the rooms with sensors; each zone's `weight` says how much it counts (0 without a sensor). `indoor_basis` is `rooms`, `sleep` (bedtime: the sleep zones), `guest` (guest zones) or `return_air` (no room sensors). `deciding_zones` are the rooms that decide what the house needs.
- `band` is the comfortable range for that zone right now (period, presence, guest mode and offset applied).
- Zones carry `paused: true` while a manual change holds autopilot for them.
- `window_open` is true once a window or door has stayed open past its delay; `door_open` says it is a door.
- Plan block kinds: `preheat`, `precool` (weather pre-conditioning) and `solar` (banking while exporting).
- `model.confident` turns true once a zone has about three hours of learning in each direction.
- `role` is what autopilot is doing with the zone, `null` while autopilot is off: `serve` (needs air), `hold` (at its aim), `shed` (ahead of the house, closed until level), `mix` (well ahead while the unit rests with its fan running: fully open so the house's air evens it out), `setback` (empty or second), `keeper` (open so the fan has enough duct), `shut` (window or door open), `held` (a person's change) or `free` (not autopilot's to move).
- `health` is `null` without a room temperature. `dew_point` and `surface_humidity` (at the coldest wall, estimated from outdoor) are `null` without a humidity reading; `damp_minutes` counts the last 12 hours. `floor` and `ceiling` are the healthy limits autopilot holds the room within.
- `house.season` is `heating`, `cooling`, `shoulder`, or `null` without any outdoor reading or forecast. `peak` is true during a heatwave's hottest hours.
- `autopilot.decision` is `null` while every behaviour is off. Its `mode` is what the house is doing (`heat`, `cool`, `dry`, or `null` while resting) and `reason` says why in plain words, as does `last_action.reason`.

## Actions

- Apply or dismiss advice: services `cyborg_hvac.apply_recommendation` and `cyborg_hvac.dismiss_recommendation` with `recommendation` (the id) and `unit_id`.
- Zones: `cover.set_cover_position`, `cover.open_cover`, `cover.close_cover` on `entities.damper`.
- Unit: the usual `climate` services on `unit.climate`.
- Guest mode and the four autopilot behaviours: `switch` services on the unit's switches.
- Hold or release autopilot: `cyborg_hvac.pause_autopilot` (minutes) and `cyborg_hvac.resume_autopilot`.

A card must ignore unknown fields and degrade when `schema` is higher than it knows.
