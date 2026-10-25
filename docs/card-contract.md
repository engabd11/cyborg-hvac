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
    "online": true, "power": true, "mode": "heat", "target": 22.0, "current": 21.0, "fan": "auto",
    "features": {"powerful": false, "eco": true, "quiet": false},
    "caps": {"modes": ["fan_only", "heat", "cool", "dry", "auto"], "fans": ["auto", "silent", "low", "medium", "high"],
             "min": 16, "max": 32, "step": 0.5, "features": ["powerful", "eco"], "zone_step": 5}
  },
  "zones": [
    {"zone": 1, "name": "MASTER", "device_id": "…", "on": true, "opening": 100,
     "temperature": 21.4, "humidity": 52, "occupied": true, "window_open": false, "guest": false,
     "band": {"low": 20.0, "high": 24.0}, "demand": 0,
     "entities": {"damper": "cover.master", "power": "switch.master_power"}}
  ],
  "house": {"period": "day", "band": {"low": 20.0, "high": 24.0}, "indoor": 21.3, "outdoor": 14.5,
            "anyone_home": true, "demand": 12, "solar_export_w": 2400, "guest_mode": false},
  "forecast": [{"time": "2026-10-25T15:00:00+11:00", "temperature": 18.0, "condition": "sunny"}],
  "advice": [{"id": "mode_heat", "category": "mode", "severity": "suggest", "title": "Heating recommended",
              "reason": "Rooms are below 20 °C. Heat to 21 °C.", "zone": null,
              "action": {"power": true, "mode": "heat", "target_temp": 21.0}}],
  "autopilot": {},
  "plan": [],
  "updated": "2026-10-25T14:02:11+11:00"
}
```

- `demand` runs from 100 (needs full heating) to the same figure in the cooling direction, sent as a negative number. The card shows it as words or colour, never with a minus sign.
- `occupied` is `null` when a zone has no presence sensor.
- `band` is the comfortable range for that zone right now (period, presence, guest mode and offset applied).
- `autopilot` and `plan` are filled in by later releases; cards treat missing data as empty.

## Actions

- Apply or dismiss advice: services `cyborg_hvac.apply_recommendation` and `cyborg_hvac.dismiss_recommendation` with `recommendation` (the id) and `unit_id`.
- Zones: `cover.set_cover_position`, `cover.open_cover`, `cover.close_cover` on `entities.damper`.
- Unit: the usual `climate` services on `unit.climate`.
- Guest mode: `switch` services on the unit's Guest mode switch.

A card must ignore unknown fields and degrade when `schema` is higher than it knows.
