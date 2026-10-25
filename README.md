# Cyborg HVAC

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Licence: MIT](https://img.shields.io/badge/Licence-MIT-green.svg)](https://github.com/engabd11/cyborg-hvac/blob/main/LICENSE)
[![Home Assistant 2025.7+](https://img.shields.io/badge/Home%20Assistant-2025.7%2B-blue.svg)](https://www.home-assistant.io)

Control your ducted air conditioner and every zone from Home Assistant, and let it run itself the way a careful owner would: the right mode for the weather, air only where it is needed, every room held even, rooms kept healthy, and as few changes as possible.

Cyborg HVAC starts with Hisense ducted systems on the ConnectLife app and is built so other brands can plug in.

This page is the full reference: every entity, option, behaviour and service. For a quick start, read [Install](#install) and [Set up](#set-up), then turn on one autopilot switch.

## Contents

- [What you get](#what-you-get)
- [Works with](#works-with)
- [Install](#install) and [Set up](#set-up)
- [Entities](#entities)
- [Options](#options): house sensors and weather, comfort targets, zone settings
- [Smart comfort](#smart-comfort): advice, autopilot, how it decides, bedtime, guests, presence, weather, solar, health, windows, airflow, manual changes, learning
- [Services](#services)
- [Dashboard card](#dashboard-card)
- [Automation examples](#automation-examples)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)

## What you get

- **The unit as a thermostat**: power, mode (cool, heat, dry, fan, auto), setpoint in half degrees, fan speed and presets.
- **Every zone as its own device**: a damper you can open, close or set from 0 to 100%, plus a power switch. Zones keep their opening when switched off, as on the wall controller.
- **The app's features as switches**: Powerful, Quiet, Sleep, Health, Energy Saving and 8 °C heat, each checked against the current mode so Home Assistant explains why a change is refused.
- **Fast, light cloud use**: one request polls every unit, push updates arrive within seconds, and changes made together (a slider across five zones) go out as one command.
- **Advice**: good practice for the house right now, from your room, presence, outdoor, forecast and solar sensors, with one tap to apply.
- **Autopilot** (each part off until you turn it on): picks the mode, setpoint, fan and every damper together. It lets rooms coast when outside will do the work, keeps the house even with few, decisive changes, follows people around the house, starts early for the weather and heatwaves, uses spare solar, and acts on mould, condensation, overheated and cold rooms.
- **A demo unit** that simulates rooms warming and cooling, for trying everything and building dashboards before you connect anything.

## Works with

| Brand | App | Tested hardware |
|---|---|---|
| Hisense ducted | ConnectLife | HACZ01 Wi-Fi zone controller with HAAZ01 zone board (8 zones) |

Other Hisense air conditioners on ConnectLife (device type 009) load with their unit controls. See [supported devices](https://github.com/engabd11/cyborg-hvac/blob/main/docs/supported-devices.md). To add another brand, see [adding a brand](https://github.com/engabd11/cyborg-hvac/blob/main/docs/adding-a-brand.md).

## Requirements

- Home Assistant 2025.7 or newer.
- A ConnectLife account with your air conditioner added in the app. Google, Apple and email sign-in all work.
- Optional, for smart comfort: room temperature (and humidity) sensors, presence or motion sensors, window and door sensors, an outdoor temperature sensor or weather forecast, person entities, and a grid power sensor. Every one is optional; anything you leave out simply switches off what needs it.

## Install

**HACS (custom repository)**

1. In HACS, open the menu, choose **Custom repositories**, add `https://github.com/engabd11/cyborg-hvac` as an **Integration**.
2. Install **Cyborg HVAC** and restart Home Assistant.

**Manual**

Copy `custom_components/cyborg_hvac` into your Home Assistant `config/custom_components` folder and restart.

## Set up

1. Go to **Settings → Devices & services → Add integration** and pick **Cyborg HVAC**.
2. Choose **Hisense (ConnectLife app)** and sign in. **Sign in with your browser** returns to Home Assistant on its own when it runs at `homeassistant.local:8123`. Otherwise choose **Sign in, then paste the address** and paste the address your browser lands on. Or choose **Demo: simulated ducted unit** to try everything without hardware.
3. Enter how many zones you use and name them as in the app.
4. Open each zone's settings (the zone under the integration) to add its room sensors and preferences, see [Zone settings](#zone-settings).
5. Open **Configure** on the integration to add house sensors and comfort targets, see [Options](#options).

Add, rename or remove zones later from the integration page.

## Entities

Each unit is a device, and each zone is its own device linked to the unit, so zones can sit in their own areas.

### Unit

| Entity | What it does |
|---|---|
| Climate | Power, mode, setpoint, fan speed and presets (Powerful, Energy saving, Sleep). The current temperature is the unit's own reading, which on ducted units is the return air. Silent appears as a fan speed on units with Quiet; choosing it turns Quiet on. |
| Powerful (switch) | Runs the compressor at full speed to reach the setpoint quickly. Heat and cool only. |
| Quiet (switch) | Lowers fan noise. Cool, heat and fan modes. |
| Energy saving (switch) | Limits output to save power. |
| Sleep (switch or select) | Eases the setpoint overnight and turns the fan down. A switch on the HACZ01; a list (general, older people, young people, children) on units with family variants. This is the unit's own sleep feature, not [Bedtime](#bedtime). |
| Health (switch) | Air purifying (HI-NANO, UVC or plasma, depending on the model). |
| 8 °C heat (switch) | Holds the home at 8 °C while away. Heat mode only. |
| Temperature offset (number) | Corrects the temperature AI mode works from, on units with AI mode (not the HACZ01). Starts disabled. |
| Return air temperature (sensor) | The unit's temperature reading, for graphs and automations. |
| Humidity, Voltage (sensors) | Where the unit reports them. The unit's humidity also stands in for room humidity sensors in the [health](#health) checks. |
| Connected (binary sensor) | Whether the unit is reachable through the cloud. |
| Fault (binary sensor) | On when the unit reports a fault; the codes are listed as attributes. |
| Filter (binary sensor) | Filter cleaning reminder. Starts disabled until its meaning is confirmed on ducted units. |
| Advice (sensor) | How many recommendations are open; the top one and the full list are attributes. See [Advice](#advice). |
| Guest mode (switch) | Puts guest zones first. See [Guest mode](#guest-mode). |
| Autopilot: presence zoning, room balancing, weather pre-conditioning, solar shifting, bedtime (switches) | Each autopilot behaviour, off until you turn it on. Their attributes show manual holds (`paused_until`, `zones_paused`) and the last change with its reason (`last_action`). See [Autopilot](#autopilot). |
| Sleep temperature (number) | The temperature bedtime holds sleep zones at. |

Switching on a feature the unit would refuse in its current mode is stopped with a clear message instead of failing silently in the cloud.

### Zone

| Entity | What it does |
|---|---|
| Damper (cover) | Open, close, or set the opening from 0 to 100%. Closing switches the zone off and keeps its opening, so opening it again restores the same airflow. Setting 0% switches the zone off. Openings round to the controller's step (5% on the HACZ01). |
| Power (switch) | Switches the zone on or off without changing its opening. |
| Temperature (sensor) | The zone's own temperature, for brands that report one per zone (the demo unit does). |
| Occupied (binary sensor) | Whether anyone is in the room, merged from its presence sensors with the presence hold applied. Only on zones with presence sensors. |

## Options

### House sensors and weather

Integration page → **Configure** → **House sensors and weather**.

| Option | Default | What it does |
|---|---|---|
| Outdoor temperature sensor | none | Outside temperature now. Used for advice, the season, [coasting](#how-autopilot-decides), and how damp cold walls get. The weather entity's temperature is used when this is empty. **The most useful input**: without any outdoor reading, autopilot cannot tell whether outside will cool or warm a room on its own. |
| Weather forecast | none | An hourly forecast (the BOM integration works well). Used for the 24 hour plan, pre-conditioning, heatwaves and cold snaps, and coasting predictions. |
| People | none | Person entities, trackers or presence sensors that are on while someone is home. With none, the house always counts as occupied. |
| Grid power sensor | none | Spots spare solar. Watts or kilowatts. |
| Grid power is negative while exporting | on | Most inverters report export as negative grid power. Turn off if yours reports export as positive. |
| Window delay | 2 min (0 to 30) | How long a window stays open before its zone counts as open to outside. |
| Door delay | 5 min (0 to 60) | The same for doors; longer, because doors are walked through. |
| Fewest open zones | 2 (1 to 4) | While the unit runs, autopilot never goes below this many open zones. Only lower it if your installer says one zone is safe. |
| Least total opening | 150% (50 to 400) | The open zones' openings added together never drop below this while the unit runs, so the fan always has enough duct. |
| Polling interval | 30 s (15 to 300) | How often the cloud is polled. Push updates arrive in between. |

### Comfort targets

Integration page → **Configure** → **Comfort targets**. Below the heating target a room is too cold; above the cooling target it is too warm; in between is comfortable. The heating target must be below the cooling target for each period.

| Option | Default | What it does |
|---|---|---|
| Heating target, day / night / away | 20 / 18 / 16 °C | The bottom of the comfort range. Autopilot aims half a degree above it when heating. |
| Cooling target, day / night / away | 24 / 25 / 28 °C | The top of the comfort range. Autopilot aims half a degree below it when cooling. |
| Highest comfortable humidity | 65% | Above this, advice suggests dry mode and autopilot dries the house (outside the heating season). |
| Night starts / Night ends | 22:00 / 06:30 | When the night targets apply. |

Away targets apply while everyone is out. An empty room in an occupied house (with presence sensors) uses the wider of the away and current ranges, so it is kept tolerable rather than comfortable.

### Zone settings

Integration page → the zone → **Reconfigure** (or set when adding a zone).

| Setting | Default | What it does |
|---|---|---|
| Name, Area | | How the zone appears in Home Assistant. |
| Room temperature sensor | none | The room's real temperature. **Needed for anything room by room**: without it the zone is only moved for presence, bedtime or airflow. |
| Room humidity sensor | none | Humidity for comfort, dry mode and [health](#health). The unit's own humidity is used when empty. |
| Presence or motion sensors | none | Whether anyone is in the room. Any one on means occupied. |
| Presence hold | 15 min (0 to 60) | How long every presence sensor must stay quiet before the room counts as empty. Still sleepers and readers often go undetected for a while. |
| Window sensors / Door sensors | none | The zone counts as open to outside once one stays open past its delay. |
| Comfort offset | 0 (−3 to +3 °C) | Shifts this room's comfort range, for example −1 for a bedroom kept a degree cooler. |
| Room type | Living area | Bedroom, living area, or utility or hallway. Utility rooms count less (40%) in the house average. Bedrooms get the night health limit (26 °C). |
| Guest zone | off | Guest mode gives this zone priority. Use it for living and dining areas. |
| Sleep zone | off | Bedtime holds this room at the sleep temperature. |
| Minimum opening | 0% | The smallest opening autopilot sets while the zone is on. Above 0, autopilot trims the zone to this instead of closing it. |
| Maximum opening | 100% | The largest opening autopilot sets. |
| Spill zone | off | May be opened to keep enough airflow when other zones close; used first for that. Mark the zone your installer set as spill on the controller (on the HACZ01 it is in the installer menu). The controller opening it by itself does not count as a manual change. |

## Smart comfort

### Advice

The **Advice** sensor and the [dashboard card](#dashboard-card) list what would help right now, most urgent first, with a plain-language reason. Most come with an action: apply it from the card or with `cyborg_hvac.apply_recommendation`; dismiss one for a while with `cyborg_hvac.dismiss_recommendation`. Advice never acts on its own. The id is what the services take; `N` is the zone number.

| Id | When it appears |
|---|---|
| `unit_offline`, `unit_fault`, `unit_filter` | The unit is offline, reports a fault, or asks for its filter to be cleaned. |
| `health_mould_N` | A room's walls have been damp enough for mould (see [Health](#health)). In winter it asks you to air the room; in summer it offers dry mode. |
| `health_condensation_N` | A room's coldest surface is at the dew point. |
| `health_overheat_N`, `health_cold_N` | A room is above or below its healthy limit. |
| `mode_heat`, `mode_cool` | Rooms are too cold or too warm and the unit is off. |
| `mode_coast` | Rooms are warm, but it is cold outside: they come back on their own (with how long it takes), so there is no need to cool. |
| `free_heating`, `free_cooling` | Outside is warmer (or cooler) than inside: open windows instead. |
| `mode_heating_warm`, `mode_cooling_cold` | Heating while rooms are too warm, or cooling while they are too cold. |
| `zone_ahead_N` | One room is well ahead while the others still need heating or cooling: close it while they catch up. |
| `mode_comfortable_off` | Rooms are comfortable and outside is mild: turn off and let the house coast. |
| `mode_avoid_auto` | The unit is in auto, which ducted units judge on return air: a fixed mode holds rooms steadier. |
| `mode_fan_mix` | Rooms differ by more than 2 degrees but are fine on average: the fan mixes them (not while humid). |
| `mode_dry` | Humidity is above your limit: dry mode. |
| `setpoint_heat_high`, `setpoint_cool_low` | Heating set to 26 °C or more, or cooling to 21 °C or less: each degree costs about 10% more and does not heat or cool faster. |
| `zoning_low_airflow` | Too few zones open for the fan. |
| `zone_window_N` | A window or door is open while its zone runs. |
| `zone_vacant_N` | A zone has been empty for 30 minutes. |
| `zone_open_N` | Someone is in a closed zone that is too cold or warm. |
| `guest_focus`, `guest_no_zones` | Guest mode is on: focus air on guest zones (or mark some). |
| `away_setback`, `away_off` | Everyone is out: set back for a short trip, turn off for a long one (setback is kept while a heatwave or cold snap is coming). |
| `weather_heatwave` | A heatwave is forecast: what to do. |
| `weather_precool`, `weather_preheat` | A hot day or cold night ahead: pre-cool or pre-heat while it is cheaper. |
| `solar_precool`, `solar_preheat` | You are exporting spare solar: bank some comfort now. |

### Autopilot

Five switches on the unit, each off until you turn it on. Turning one on acts straight away.

| Switch | What it does |
|---|---|
| Autopilot: room balancing | Hands the unit to the autopilot engine: the mode, setpoint, fan and every damper, so every room with a sensor holds its range and the house stays even. See [How autopilot decides](#how-autopilot-decides). |
| Autopilot: bedtime | The same engine, focused on sleep zones at the sleep temperature. See [Bedtime](#bedtime). |
| Autopilot: presence zoning | Rooms empty for 30 minutes (after the presence hold) only need to stay tolerable and get little or no air; rooms people walk into get theirs back. While everyone is out the house drops to the away targets, and switches off after 2 hours (not during a heatwave or cold snap). See [Presence and away](#presence-and-away). |
| Autopilot: weather pre-conditioning | Uses the hourly forecast and what it has learned about your rooms to start early, so rooms are comfortable when night or morning begins, or before a heat spike or heatwave. See [Weather](#weather-heatwaves-and-cold-snaps). |
| Autopilot: solar shifting | While you export spare solar, cools or warms up to a degree further within comfort, and stops when the surplus ends. See [Spare solar](#spare-solar). |

With room balancing or bedtime on, presence, the forecast, solar and health all feed the one engine. Without them, presence, pre-conditioning and solar keep a lighter touch: they start, set back and restore the unit, and leave the dampers to you (presence still closes empty rooms). [Health](#health) protection runs whenever any switch is on.

### How autopilot decides

The engine runs every minute and picks the gentlest lever that gets each room where it should be, in this order:

1. **Let it coast.** A room past its aim that outside will bring back on its own within 2 hours (cooling down on a cold night, warming up on a mild day) is closed and left to drift, rather than switching heating to cooling. The prediction uses what autopilot has learned about the room and the forecast, and is checked against what happens: a room that does not move as predicted (sun, people, a heater) stops counting as coasting.
2. **Move a damper** when rooms differ from each other. A room behind the others gets more air, a room holding its aim gets its learned holding opening, and a room well ahead is closed until it is level with the rest of the house, then rejoins.
3. **Move the setpoint** when the house as a whole is off. The unit regulates on return air, so the setpoint is corrected for how far that reads from the rooms, with a gentle push while a room is short and a slow correction that learns any steady error.
4. **Change mode** only when nothing gentler works.
5. **Rest**: when every room will stay comfortable for a while, the unit switches off rather than idling, and starts again when a room needs it.

The dashboard card shows what autopilot is doing and why (for example "Heating HQ (19.5 °C). Letting LIVING (24 °C) cool on its own (8 °C outside).") and each zone's role. The same is in the websocket feed and in diagnostics.

| Role | Meaning |
|---|---|
| serve | Behind its aim: more air, up to fully open. |
| hold | At its aim: its learned holding opening. |
| shed | Ahead of the house: closed until it is level with the other rooms. |
| setback | Empty, or second while guests or sleepers come first: closed, or a small trickle (20 to 30%) if it would get too cold or warm. |
| keeper | Kept open so the fan has enough duct. |
| shut | A window or door is open. |
| held | A person changed it; left alone for 2 hours. |
| free | Not autopilot's to move (no sensor and nothing asks for it). |

**Few, decisive changes.** The limits autopilot works within:

| What | Limit |
|---|---|
| A room counts as needing air | half a degree short of its aim |
| A room is closed for being ahead | 1.5 degrees past its aim (0.5 at bedtime), and only while other rooms still need the unit |
| Damper moves | straight to the target in one move (100% to 60%, not four steps); then at least 15 minutes before it moves again; moves under 20% wait until the zone opens or closes |
| Setpoint changes | by 1 degree or more (half a degree at bedtime), at most every 15 minutes, except to ease off once rooms overshoot |
| Fan changes | at most every 30 minutes |
| Starting from rest | a room half a degree short for 5 minutes, or a degree short straight away |
| Heating to cooling or back | a room too warm (or cold) for 20 minutes that outside will not fix, nothing else still needing the current mode, an hour in the current mode, and in the heating season a room 1.5 degrees further past its range before cooling is used (and the other way round) |
| Never | auto mode (it is replaced with heat or cool); heating set above 25.5 °C or cooling below 21.5 °C unless your targets ask for it |
| Guard rails, whatever the engine wants | 8 minutes between compressor on and off; 30 minutes between mode changes; 5 minutes between setpoint writes; 2 minutes between moves of one zone; at most 6 commands in 10 minutes; the airflow floor; manual holds |

Everything goes out as one command. Openings are sent before closings, so the fan never sees too little duct.

**Fan**: auto normally; low when cooling humid air (a slow fan dries better) and at bedtime (quieter, medium while the bedroom is well short); high during a heatwave's hottest hours or when the house is 2 degrees behind; at most medium when less duct is open than the airflow floor. A fan speed someone picks is left alone for 2 hours, and the fan is left alone while Quiet is on.

### Bedtime

Turn on **Autopilot: bedtime** at bedtime (by hand or with an [automation](#automation-examples)) and set **Sleep temperature**. Sleep zones are held within half a degree of it whatever the rest of the house does: the unit's mode and setpoint follow the bedroom, and the other zones close down as far as the airflow floor allows, keeping the spill zone as the second air path, with a small trickle for any that would get too cold or warm. Sleep zones always count as occupied, and the other zones count as empty while bedtime is on.

If the bedroom is warmer than the sleep temperature on a cold night, bedtime does not cool it: the unit rests (or idles) while outside brings the bedroom down, then holds it there. With only the bedroom being conditioned, the setpoint does the fine work and its damper stays fully open. The fan runs low. Without a bedroom temperature sensor, bedtime can only hold the unit at the sleep temperature.

### Guest mode

Mark the living and dining zones as guest zones. With **Guest mode** on, they get full comfort and priority (they count 1.5 times as much in the house average), and the other zones only need to stay tolerable, so the unit works for the rooms your guests are in.

### Presence and away

A room with presence sensors is empty once every sensor has been quiet for its presence hold. After 30 minutes empty, presence zoning closes it (or trims it to its minimum opening) and its comfort range widens towards the away range; someone walking in gets the room back. Empty rooms count for less (30%) in the house average.

While everyone is out (all your people away), the house uses the away targets. With presence zoning on, the unit switches off after 2 hours away, unless a heatwave or cold snap is forecast, when it holds the away targets instead: a house left to 35 °C or 10 °C takes hours to recover. When someone comes home the normal targets return.

### Weather, heatwaves and cold snaps

**Season**: from the outdoor temperature and the next 24 hours of forecast. Heating season when the day's average is more than 2 degrees below your day heating target; cooling season when it is within 2 degrees of your day cooling target or above; mild in between. The season decides which mode needs the extra margin before autopilot changes to it.

**Pre-conditioning** (weather pre-conditioning on): before night starts and before morning, autopilot predicts where rooms would drift by then and, if they would leave the comfort range, starts early enough to have them in range in time (with a 25% margin). A forecast 8 degrees above your day cooling target within 12 hours gets a pre-cool to a degree below the target, finishing 3 hours before the peak. The plan is shown on the card.

**Heatwave**: a forecast of 35 °C or more, or 10 degrees above your day cooling target if that is higher. Pre-cooling starts twice as early, in the cool of the morning, when the unit works far better. During the hottest hours (outside 6 degrees above your cooling target) autopilot focuses air on occupied rooms, runs the fan high, accepts rooms at the top of the range, and never sets cooling more than 2 degrees below your cooling target: lower does not cool faster and only overshoots once the heat breaks. Advice suggests closing blinds and doors.

**Cold snap**: a forecast 13 degrees below your night heating target (5 °C with the defaults). Away keeps the setback instead of switching off.

### Spare solar

With solar shifting on and a grid power sensor set, exporting 1.5 kW or more starts banking comfort: cooling a degree further down when it is hot outside, or heating a degree further up when it is cold, always within your comfort range. It stops when export drops below 600 W or everyone leaves. The banking shows on the card's 24 hour plan.

### Health

Autopilot watches every room with a temperature sensor, and with a humidity reading it also watches the walls. While any autopilot switch is on it acts on these even where your settings or presence would not; the advice flags them either way.

| Condition | When | What autopilot does |
|---|---|---|
| Mould risk | The coldest surface has been at 70% humidity or more for 2 hours in the last 12, or is at 85% or more now. Surface humidity is estimated from room humidity and how far the walls sit between room and outdoor temperature. | Outside the heating season: dry mode (or cooling with a slow fan when rooms are warm). In winter: keeps the room warm enough for its walls to dry, never beyond normal comfort, and the advice asks you to air it (heating alone does not remove moisture). |
| Condensation | The coldest surface is at the dew point. | Flags it, and the mould handling above. |
| Too hot | An occupied room above 28 °C, a bedroom above 26 °C at night, an empty room above 30 °C, or an empty house above 32 °C. | Cools it, even if presence or eco settings would not. |
| Too cold | An occupied room below 16 °C (15 °C at night), or any room below 12 °C. | Heats it. |

Dry mode starts once humidity has stayed above your limit for 15 minutes, and stops when it is 5 points below the limit or rooms would get too cool. It is never used in the heating season.

### Windows and doors

A window counts as open after its delay (2 minutes) and a door after its delay (5 minutes), so walking through a sliding door does nothing. Once one stays open, the advice suggests closing that zone, and any autopilot that is on closes it and leaves it out of balancing, then puts it back as it was when the window or door shuts. If every open zone is open to outside, the unit pauses until one closes. A sleep zone's window is left to the sleeper at bedtime.

### Airflow floor and spill zones

While the unit runs, autopilot keeps at least 2 zones open with their openings adding up to 150% or more, so the fan always has enough duct. To make up the difference it uses the spill zone first, then zones that are already open (so nothing has to move), then empty or second rooms closest to needing air, and rooms that are ahead only as a last resort and only as much as needed. Both limits are adjustable in the house settings. A spill zone set by your installer already protects the fan; only lower the floor if your installer confirms one zone on its own is safe.

### Manual changes

If someone changes a zone, the unit or its fan in Home Assistant, the ConnectLife app or on the wall controller, autopilot leaves that alone for 2 hours (changes autopilot made itself are recognised and never count). The controller opening a spill zone by itself does not count either. The card marks held zones with a hand. **Resume** (or `cyborg_hvac.resume_autopilot`) ends holds early; **Pause 2 h** (or `cyborg_hvac.pause_autopilot`) holds everything.

### Learning

Cyborg HVAC learns, per room, how fast it loses heat to outside and how quickly the unit warms or cools it. It starts from typical values and becomes confident after a few hours of each, which makes pre-conditioning start at the right time and coasting predictions accurate. It also learns each room's **holding opening**: how much air keeps it level with the rest of the house, in heating and in cooling. A room that keeps drifting ahead of the others gets less next time, one that falls behind gets more (a bigger step the further out it is), so the dampers settle and move less over time. What it learns survives restarts.

## Services

| Service | Fields | What it does |
|---|---|---|
| `cyborg_hvac.apply_recommendation` | `recommendation` (an advice id), `unit_id`, `entry_id` | Carries out a recommendation's action and hides it for 10 minutes while the unit catches up. |
| `cyborg_hvac.dismiss_recommendation` | `recommendation`, `minutes` (default 240, up to 10080), `unit_id`, `entry_id` | Hides a recommendation for a while. |
| `cyborg_hvac.pause_autopilot` | `minutes` (default 120, up to 1440), `unit_id`, `entry_id` | Holds every autopilot behaviour and zone. Without a unit, every unit. |
| `cyborg_hvac.resume_autopilot` | `unit_id`, `entry_id` | Ends pauses and manual holds early. |

`unit_id` and `entry_id` are only needed with more than one unit or integration entry.

## Dashboard card

The **HVAC card** in [Cyborg Cards](https://github.com/engabd11/cyborg-cards) (`custom:cyborg-hvac-card`, version 1.4.4 or newer for everything here) shows the unit, every zone with its role and health, advice with Apply and Dismiss, the 24 hour plan, and the autopilot switches, with what autopilot is doing and why.

The card reads one live feed per unit over the websocket instead of rebuilding the logic in the browser, so any card or tool can use it. See the [card contract](https://github.com/engabd11/cyborg-hvac/blob/main/docs/card-contract.md).

## Automation examples

Bedtime at 10 pm, off in the morning:

```yaml
automation:
  - alias: Bedtime on
    triggers:
      - trigger: time
        at: "22:00:00"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.duct_air_conditioner_autopilot_bedtime
  - alias: Bedtime off
    triggers:
      - trigger: time
        at: "06:30:00"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.duct_air_conditioner_autopilot_bedtime
```

Get a notification when a room is at risk of mould:

```yaml
automation:
  - alias: Mould risk
    triggers:
      - trigger: state
        entity_id: sensor.duct_air_conditioner_advice
        attribute: recommendations
    conditions:
      - condition: template
        value_template: >
          {{ state_attr('sensor.duct_air_conditioner_advice', 'recommendations')
             | selectattr('id', 'search', '^health_mould_') | list | count > 0 }}
    actions:
      - action: notify.notify
        data:
          message: >
            {{ (state_attr('sensor.duct_air_conditioner_advice', 'recommendations')
                | selectattr('id', 'search', '^health_mould_') | first).reason }}
```

Entity ids depend on your unit's name; check them under the device.

## Troubleshooting

- **Autopilot is not changing a zone or the unit.** Someone changed it in the last 2 hours (the card shows a hand, and the autopilot switches' `zones_paused` and `paused_until` attributes say until when). Press **Resume**. Also check the window and door sensors: an open zone is left closed.
- **It will not switch from heating to cooling.** Usually on purpose: outside will bring the room back on its own (the reason says "Letting … cool on its own"), the unit changed mode less than an hour ago, or it is the heating season and the room is not far enough past its range. Without an outdoor sensor or forecast, autopilot needs a larger difference before switching.
- **A room is closed even though it is only a bit warm.** It is ahead of the others while they still need heating, and rejoins once it is level with them (role `shed`).
- **Why did it do that?** The autopilot switches' `last_action` attribute has the time, the command and the reason; the card shows the current decision. [Diagnostics](https://www.home-assistant.io/docs/configuration/troubleshooting/#download-diagnostics) include autopilot's state and decision and the unit's raw status, with identifiers removed.
- **Room by room features do nothing.** The zone needs a room temperature sensor.
- **The sign-in redirect does not come back.** Use **Sign in, then paste the address**.

## Documentation

- [Smart comfort guide](https://github.com/engabd11/cyborg-hvac/blob/main/docs/smart-comfort.md)
- [Entities](https://github.com/engabd11/cyborg-hvac/blob/main/docs/entities.md)
- [Card contract](https://github.com/engabd11/cyborg-hvac/blob/main/docs/card-contract.md) (the websocket feed)
- [Supported devices](https://github.com/engabd11/cyborg-hvac/blob/main/docs/supported-devices.md)
- [Adding a brand](https://github.com/engabd11/cyborg-hvac/blob/main/docs/adding-a-brand.md)
- [Development](https://github.com/engabd11/cyborg-hvac/blob/main/docs/development.md)

## Licence and credits

MIT. Parts of the ConnectLife client are adapted from Hisense's own MIT-licensed Home Assistant plugin, and the HACZ01 zone properties were first documented by the community `connectlife` project. See [NOTICE](https://github.com/engabd11/cyborg-hvac/blob/main/NOTICE).

Cyborg HVAC is an independent project, not affiliated with Hisense or ConnectLife.
