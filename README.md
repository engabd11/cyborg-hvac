# Cyborg HVAC

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Licence: MIT](https://img.shields.io/badge/Licence-MIT-green.svg)](https://github.com/engabd11/cyborg-hvac/blob/main/LICENSE)
[![Home Assistant 2025.7+](https://img.shields.io/badge/Home%20Assistant-2025.7%2B-blue.svg)](https://www.home-assistant.io)

Control your ducted air conditioner and every zone from Home Assistant, and let it run itself the way a careful owner would: the right mode for the weather, air only where it is needed, every room held even, rooms kept healthy, and as few changes as possible.

Cyborg HVAC starts with Hisense ducted systems on the ConnectLife app and is built so other brands can plug in.

This page is the full reference: every entity, option, behaviour and service. For a quick start, read [Install](#install) and [Set up](#set-up), then turn on the **Climate Butler**.

## Contents

- [What you get](#what-you-get)
- [Works with](#works-with)
- [Install](#install) and [Set up](#set-up)
- [Entities](#entities)
- [Options](#options): house sensors and weather, comfort targets, zone settings
- [Smart comfort](#smart-comfort): advice, the Climate Butler, how it decides, energy, ventilation, bedtime, guests, presence, weather, health, windows, airflow, manual changes, learning, the journal
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
- **The Climate Butler** (off until you turn it on): picks the mode, setpoint, fan and every damper together. It watches how fast each room is really warming or cooling and learns what each room does by itself (a bedroom with people asleep, a sunny lounge), so it lets outside do the work only while that is actually happening and steps in when it is not. It keeps the house even with few, decisive changes, keeps air moving through a comfortable house while the battery has charge, follows people around the house, starts early for the weather and heatwaves, and acts on mould, condensation, overheated and cold rooms. **Bedtime** holds the bedroom within half a degree all night.
- **Energy** (Auto, Eco, Mid or Max): how much comfort is worth right now, set by hand or followed from your solar, battery and grid sensors. On Max (exporting, or plenty of solar) every room is kept near the middle of its range, even empty ones and while you are out.
- **A journal** of everything the Butler did and why, on the card and in Home Assistant's history.
- **A demo unit** that simulates rooms warming and cooling, for trying everything and building dashboards before you connect anything.

## Works with

| Brand | App | Tested hardware |
|---|---|---|
| Hisense ducted | ConnectLife | HACZ01 Wi-Fi zone controller with HAAZ01 zone board (8 zones) |

Other Hisense air conditioners on ConnectLife (device type 009) load with their unit controls. See [supported devices](https://github.com/engabd11/cyborg-hvac/blob/main/docs/supported-devices.md). To add another brand, see [adding a brand](https://github.com/engabd11/cyborg-hvac/blob/main/docs/adding-a-brand.md).

## Requirements

- Home Assistant 2025.7 or newer.
- A ConnectLife account with your air conditioner added in the app. Google, Apple and email sign-in all work.
- Optional, for smart comfort: room temperature (and humidity) sensors, presence or motion sensors, window and door sensors, an outdoor temperature sensor or weather forecast, person entities, and grid, solar and battery sensors. Every one is optional; anything you leave out simply switches off what needs it.

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
| Climate Butler (switch) | Hands the unit to the Butler: mode, setpoint, fan and every damper. See [Climate Butler](#climate-butler). Its attributes, like the other three switches', show manual holds (`paused_until`, `zones_paused`) and the last change with its reason (`last_action`). Called *Autopilot: room balancing* before 0.7; its entity id stays the same. |
| Bedtime (switch) | Holds the sleep zones within half a degree of the sleep temperature. See [Bedtime](#bedtime). |
| Climate Butler: presence zoning, Climate Butler: weather pre-conditioning (switches) | What the Butler takes into account, each off until you turn it on. See [Climate Butler](#climate-butler). |
| Energy (select) | Auto, Eco, Mid or Max. Its attributes say which mode is in use (`active`) and why (`reason`). See [Energy](#energy). Replaces *Autopilot: solar shifting*, which is removed. |
| Climate Butler activity (sensor) | What the Butler is doing in one word: heating, cooling, drying, ventilating, idling, watching, resting, away, holding, paused or off, with the headline, the reason and the rooms it is watching as attributes. Home Assistant's history and logbook keep every change. |
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
| Outdoor temperature sensor | none | Outside temperature now. Used for advice, the season, [coasting](#how-the-butler-decides), and how damp cold walls get. The weather entity's temperature is used when this is empty. **The most useful input**: without any outdoor reading, autopilot cannot tell whether outside will cool or warm a room on its own. |
| Weather forecast | none | An hourly forecast (the BOM integration works well). Used for pre-conditioning, heatwaves and cold snaps, coasting predictions, and what the card shows coming up. |
| People | none | Person entities, trackers or presence sensors that are on while someone is home. With none, the house always counts as occupied. |
| Grid power sensor | none | Spots export to the grid. Watts or kilowatts. |
| Grid power is negative while exporting | on | Most inverters report export as negative grid power. Turn off if yours reports export as positive. |
| Solar power sensor | none | What the panels make now. Watts or kilowatts. |
| Battery power sensor | none | Power into or out of the house battery. Watts or kilowatts. |
| Battery power is negative while charging | on | Turn off if your battery reports charging as positive. |
| Battery charge sensor | none | The house battery's charge in percent. Decides [ventilation](#ventilation). |
| Plenty of solar | 6 kW (0.5 to 50) | Solar output that counts as free power (Max), even without exporting. |
| Keep air moving above | 30% (0 to 100) | In mild weather, while every room is comfortable, the fan keeps air moving through the house as long as the battery is above this. See [Ventilation](#ventilation). |
| Window delay | 2 min (0 to 30) | How long a window stays open before its zone counts as open to outside. |
| Door delay | 5 min (0 to 60) | The same for doors; longer, because doors are walked through. |
| Fewest open zones | 2 (1 to 4) | While the unit runs, autopilot never goes below this many open zones. Only lower it if your installer says one zone is safe. |
| Least total opening | 150% (50 to 400) | The open zones' openings added together never drop below this while the unit runs, so the fan always has enough duct. |
| Polling interval | 30 s (15 to 300) | How often the cloud is polled. Push updates arrive in between. |

### Comfort targets

Integration page → **Configure** → **Comfort targets**. Below the heating target a room is too cold; above the cooling target it is too warm; in between is comfortable. The heating target must be below the cooling target for each period.

| Option | Default | What it does |
|---|---|---|
| Heating target, day / night / away | 20 / 18 / 16 °C | The bottom of the comfort range. On Mid the Butler aims half a degree above it when heating ([Energy](#energy) moves the aim). |
| Cooling target, day / night / away | 24 / 25 / 28 °C | The top of the comfort range. On Mid the Butler aims half a degree below it when cooling. |
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
| `solar_precool`, `solar_preheat` | You are exporting spare solar and the unit is off: bank some comfort now (with the Butler on, set Energy to Auto or Max instead). |

### Climate Butler

Four switches and a select on the unit. Each switch is off until you turn it on, and turning one on acts straight away: it also ends any hold left by an earlier manual change, because switching it on hands the unit over.

| Control | What it does |
|---|---|
| Climate Butler | Hands the unit to the Butler: the mode, setpoint, fan and every damper, so every room with a sensor holds its range and the house stays even. See [How the Butler decides](#how-the-butler-decides). |
| Bedtime | The same engine, holding sleep zones within half a degree of the sleep temperature. See [Bedtime](#bedtime). |
| Climate Butler: presence zoning | Rooms empty for 30 minutes (after the presence hold) only need to stay tolerable and get little or no air; rooms people walk into get theirs back. While everyone is out the house drops to the away targets, and switches off after 2 hours (not during a heatwave or cold snap, and not on Max). See [Presence and away](#presence-and-away). |
| Climate Butler: weather pre-conditioning | Uses the hourly forecast and what the Butler has learned about your rooms to start early, so rooms are comfortable when night or morning begins, or before a heat spike or heatwave. See [Weather](#weather-heatwaves-and-cold-snaps). |
| Energy (select) | How much comfort is worth: Auto, Eco, Mid or Max. See [Energy](#energy). |

With the Climate Butler or Bedtime on, presence, the forecast, the energy mode and health all feed the one engine. Without them, presence and pre-conditioning keep a lighter touch: they start, set back and restore the unit, and leave the dampers to you (presence still closes empty rooms). [Health](#health) protection runs whenever any switch is on.

### How the Butler decides

The engine runs every minute and picks the gentlest lever that gets each room where it should be, in this order:

1. **Let it coast, while it works.** A room past its aim that outside will bring back on its own soon (cooling down on a cold night, warming up on a mild day) is left to drift rather than switching heating to cooling. The prediction uses what the Butler has learned about the room, its own warmth included, and the forecast. The room gets a fixed deadline (half as long again as predicted, at least 15 minutes, at most the energy mode's limit). After 15 minutes (10 at bedtime) its measured trend must agree: a room that is not moving, or too slowly to make the deadline, is handed to the unit there and then, and stays handed until it is back in range. While other rooms are being served, or when a change of mode rides on it, a coasting room is closed, so its trend shows what the room really does.
2. **Move a damper** when rooms differ from each other. A room behind the others gets more air, a room holding its aim gets its learned holding opening, and a room well ahead is closed until it is level with the rest of the house, then rejoins.
3. **Move the setpoint** when the house as a whole is off. The unit regulates on return air, so the setpoint is corrected for how far that reads from the rooms, with a gentle push while a room is short and a slow correction that learns any steady error. Its limits move with that correction too: cooling one warm bedroom while the rest of the house is cold, the unit reads cold return air and is set lower than the bedroom's aim, or it would never cool it.
4. **Change mode** only when nothing gentler works.
5. **Hold, keep air moving, or rest**: when every room will stay comfortable for a while, what happens depends on the weather (see [Ventilation](#ventilation)). In mild weather the fan alone keeps air moving through the house if there is power to spare. In cold or hot weather the unit stays in heating or cooling, from half a battery up or on Max: at its setpoint it rests its compressor, its fan keeps going, and it picks up again by itself as soon as the rooms dip. Otherwise the unit switches off, and starts again the moment a room needs it.

**While the unit rests but its fan runs** (its own reading has sat past its setpoint for 10 minutes, so the compressor is off), the fan's air is just the house's air going round. Rooms well ahead of the rest (too warm while heating, too cool while cooling) get their damper fully open, so that air evens them out for free, while the other rooms keep their opening. The moment a room needs the unit again, those rooms go back to normal (closed if they are well ahead) in the same command. Not during a heatwave's hottest hours.

The dashboard card shows what the Butler is doing and why (for example "Letting LIVING (24 °C) cool on its own (8 °C outside, falling 0.6 °C an hour); cooling it at 23:40 if not."), the rooms it is watching, and each zone's role. The same is in the websocket feed, the activity sensor and diagnostics, and every decision goes into the [journal](#journal).

| Role | Meaning |
|---|---|
| serve | Behind its aim: more air, up to fully open. |
| hold | At its aim: its learned holding opening. |
| shed | Ahead of the house: closed until it is level with the other rooms. Also a room left to coast while the unit serves others. |
| mix | Well ahead (1 degree or more past its aim) while the unit rests with its fan still running: fully open, so air from the rest of the house evens it out for free. Ends the moment the unit works again. |
| vent | Every room is comfortable and the fan keeps air moving: fully open. |
| setback | Empty, or second while guests or sleepers come first: closed, or a small trickle (20 to 30%) if it would get too cold or warm. |
| keeper | Kept open so the fan has enough duct. |
| shut | A window or door is open. |
| held | A person changed it; left alone for 2 hours. |
| free | Not autopilot's to move (no sensor and nothing asks for it). |

**Few, decisive changes.** The limits the Butler works within:

| What | Limit |
|---|---|
| A room counts as needing air | half a degree short of its aim (0.3 on Max and at bedtime) |
| A room is closed for being ahead | 1.5 degrees past its aim (0.5 at bedtime), and only while other rooms still need the unit |
| Damper moves | straight to the target in one move (100% to 60%, not four steps); then at least 15 minutes before it moves again; moves under 20% wait until the zone opens or closes |
| Setpoint changes | by 1 degree or more (half a degree at bedtime), at most every 15 minutes, except to ease off once rooms overshoot |
| Fan changes | at most every 30 minutes |
| Starting from rest | a room short for 5 minutes, or a degree short straight away; at once for a room whose coast did not work, and for the bedroom at bedtime |
| Heating to cooling or back | a room too warm (or cold) that outside is not fixing, nothing else still needing the current mode, and the energy mode's wait (see [Energy](#energy)); straight away once a coasting room has been seen not to come back. In the season against it, a room must also be further past its range (2 degrees on Eco, 1.5 on Mid, a quarter on Max, none at bedtime) |
| Never | auto mode (it is replaced with heat or cool); heating set above 25.5 °C or cooling below 21.5 °C, unless your targets or cold return air ask for it |
| Guard rails, whatever the engine wants | 8 minutes between compressor on and off; 30 minutes between heating and cooling (8 to or from fan only or dry); 5 minutes between setpoint writes; 2 minutes between moves of one zone; at most 6 commands in 10 minutes; the airflow floor; manual holds |

Everything goes out as one command. Openings are sent before closings, so the fan never sees too little duct.

**Fan**: auto normally; low when cooling humid air (a slow fan dries better) and at bedtime (quieter, medium while the bedroom is a degree short); high during a heatwave's hottest hours or when the house is well behind (2 degrees on Eco and Mid, 1 on Max); at most medium when less duct is open than the airflow floor. While only moving air it runs low (medium on Max). A fan speed someone picks is left alone for 2 hours, and the fan is left alone while Quiet is on.

### Bedtime

Turn on **Bedtime** at bedtime (by hand or with an [automation](#automation-examples)) and set **Sleep temperature**. Sleep zones are held within half a degree of it whatever the rest of the house does and whatever the energy mode: the unit's mode and setpoint follow the bedroom, and the other zones close down as far as the airflow floor allows, keeping the spill zone as the second air path, with a small trickle for any that would get too cold or warm. Sleep zones always count as occupied, and the other zones count as empty while bedtime is on.

Bedtime acts early: heating or cooling starts once the bedroom is 0.3 degrees off, or as soon as its trend says it will leave its half degree within 15 minutes. If the bedroom is warm on a cold night, it is left to cool on its own only if outside will have it back within 45 minutes; the unit stops sending it warm air meanwhile, and after 10 minutes its trend must show it really is cooling. A closed bedroom with people asleep often is not, and then bedtime cools it, even on a cold night, rather than waiting. What it learns about the bedroom's own warmth at night makes it skip the wait next time.

With only the bedroom being conditioned, the setpoint does the fine work and its damper stays fully open. The fan runs low. Without a bedroom temperature sensor, bedtime can only hold the unit at the sleep temperature.

### Guest mode

Mark the living and dining zones as guest zones. With **Guest mode** on, they get full comfort and priority (they count 1.5 times as much in the house average), and the other zones only need to stay tolerable, so the unit works for the rooms your guests are in.

### Presence and away

A room with presence sensors is empty once every sensor has been quiet for its presence hold. After 30 minutes empty, presence zoning closes it (or trims it to its minimum opening) and its comfort range widens towards the away range; someone walking in gets the room back. Empty rooms count for less (30%) in the house average.

While everyone is out (all your people away), the house uses the away targets. With presence zoning on, the unit switches off after 2 hours away, unless a heatwave or cold snap is forecast, when it holds the away targets instead: a house left to 35 °C or 10 °C takes hours to recover. On Max the house keeps its normal comfort while you are out, since the power is free. When someone comes home the normal targets return.

### Weather, heatwaves and cold snaps

**Season**: from the outdoor temperature and the next 24 hours of forecast. Heating season when the day's average is more than 2 degrees below your day heating target; cooling season when it is within 2 degrees of your day cooling target or above; mild in between. The season decides which mode needs the extra margin before autopilot changes to it.

**Pre-conditioning** (weather pre-conditioning on): before night starts and before morning, autopilot predicts where rooms would drift by then and, if they would leave the comfort range, starts early enough to have them in range in time (with a 25% margin). A forecast 8 degrees above your day cooling target within 12 hours gets a pre-cool to a degree below the target, finishing 3 hours before the peak. The plan is shown on the card.

**Heatwave**: a forecast of 35 °C or more, or 10 degrees above your day cooling target if that is higher. Pre-cooling starts twice as early, in the cool of the morning, when the unit works far better. During the hottest hours (outside 6 degrees above your cooling target) the Butler focuses air on occupied rooms, runs the fan high, accepts rooms at the top of the range, and never sets cooling more than 2 degrees below your cooling target: lower does not cool faster and only overshoots once the heat breaks. On Max it keeps every room near the middle of its range instead, and goes down to 3 degrees below. Advice suggests closing blinds and doors.

**Cold snap**: a forecast 13 degrees below your night heating target (5 °C with the defaults). Away keeps the setback instead of switching off.

### Energy

The **Energy** select says how much comfort is worth right now. Set it by hand, or leave it on **Auto** to follow your solar, battery and grid sensors.

| Mode | When Auto picks it | What it means |
|---|---|---|
| Eco | Running on the battery or the grid, little solar | Rooms may sit nearer the edges of their range (0.3 degrees inside); outside gets up to 3 hours to bring a room back; heating and cooling change over only after a room has stayed out for 20 minutes, at most once an hour, and 2 degrees further out in the season against it. |
| Mid | Solar charging the battery (at least 300 W of solar and 200 W into the battery; without a battery sensor, 1.5 kW of solar). Also used with no solar or battery sensors at all. | The everyday balance: rooms aim half a degree inside their range; outside gets up to 2 hours; changeovers as on Eco but 1.5 degrees further out. |
| Max | Exporting 300 W or more, or solar at or above **Plenty of solar** (6 kW) | Comfort comes first, because the power is free: every room aims within half a degree of the middle of its range, empty and second rooms included, and while everyone is out; rooms count as needing air 0.3 degrees off; outside gets 30 minutes; changeovers after 10 minutes, at most every half hour; the fan goes high at a degree behind. |

A new mode must last 10 minutes before Auto uses it, so a passing cloud changes nothing, and Max stays while export is above 100 W or solar within 1 kW of plenty. The select's attributes, the activity sensor and the card say which mode is in use and why, for example "Exporting 3.4 kW: comfort comes first". Bedtime holds the bedroom within half a degree whatever the mode.

### Ventilation

When every room is comfortable and nothing needs heating or cooling for a while, the Butler runs the fan only rather than switching off, so air keeps moving through the house: rooms stay fresh, and warm or cool spots even out with the rest of the house for the price of the fan. It does this while the house battery is above **Keep air moving above** (30%), starting again only once it is 3 points above that; without a battery charge sensor, only on Max. Not while drying, and not while everyone is out (except on Max).

Every zone opens fully, except a room the house's mixed air would push out of its range (one within 0.3 degrees of the edge on that side), which closes until it is 0.6 inside again. The fan runs low (medium on Max, low at bedtime). The moment a room needs heating or cooling, the unit switches straight back.

**Only in mild weather.** Fan-only just sends the house's own air round the ducts with nothing behind it, so on a cold day the house cools faster than it would at rest (and on a hot day it warms). The fan runs alone only while outside stays within 2 degrees of your comfort range for the next hour. In colder or hotter weather a comfortable house instead stays in heating or cooling, as long as the battery is at least half full (or on Max): at its setpoint the unit rests its compressor, keeps its fan going, and heats or cools again by itself as soon as the rooms dip. Below half a battery the unit switches off. If the fan is already running when the weather turns, heating or cooling takes over, or the unit switches off. Without any outdoor reading or forecast, ventilation works as above.

### Health

The Butler watches every room with a temperature sensor, and with a humidity reading it also watches the walls. While any switch is on it acts on these even where your settings, presence or the energy mode would not; the advice flags them either way.

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

If someone changes a zone, the unit or its fan in Home Assistant, the ConnectLife app or on the wall controller, the Butler leaves that alone for 2 hours (changes it made itself are recognised and never count), and notes it in the journal. The controller opening a spill zone by itself does not count either. The card marks held zones with a hand. **Resume** (or `cyborg_hvac.resume_autopilot`) ends holds early; **Pause 2 h** (or `cyborg_hvac.pause_autopilot`) holds everything. Switching the Climate Butler, Bedtime or another behaviour on also ends the holds on the unit and fan: that is handing it over.

### Learning

Cyborg HVAC learns, per room, how fast it loses heat to outside, how quickly the unit warms or cools it, and how much warmth (or chill) the room adds by itself, separately by day and by night: people asleep with the door shut, a sunny window, a computer. Loss and the room's own warmth are learned together from how the room drifts while the unit is off, and told apart as the gap to outside changes through the day. It starts from typical values and becomes confident after a few hours of each, which makes pre-conditioning start at the right time and coasting predictions accurate. Once a room's own warmth reaches about 0.3 degrees an hour, the journal says so ("Learned: MASTER keeps itself warm at night"), and the Butler stops waiting for outside to cool that room.

Alongside the model, the Butler measures each room's trend: how fast it is moving now, from its readings since the last change in what it gets (the unit's mode, its damper opening or closing). That is what a coasting room is checked against, what Bedtime looks ahead with, and what the card shows.

It also learns each room's **holding opening**: how much air keeps it level with the rest of the house, in heating and in cooling. A room that keeps drifting ahead of the others gets less next time, one that falls behind gets more (a bigger step the further out it is), so the dampers settle and move less over time. What it learns survives restarts.

### Journal

Everything the Butler does goes into its journal: each command with what it set ("Cool", "22.5°", "Bedroom 100%", "Living closed") and why; plans it made without a change (starting to watch a room, resting); energy mode changes; your own changes it is leaving alone; behaviours switched on or off; and what it has learned about your rooms. The same plan is not written twice within half an hour. The journal keeps 7 days (up to 500 entries) and survives restarts. The card shows the latest and loads the rest on demand (websocket command `cyborg_hvac/journal`), and the **Climate Butler activity** sensor puts every change of activity in Home Assistant's history and logbook, for as long as you keep history.

## Services

| Service | Fields | What it does |
|---|---|---|
| `cyborg_hvac.apply_recommendation` | `recommendation` (an advice id), `unit_id`, `entry_id` | Carries out a recommendation's action and hides it for 10 minutes while the unit catches up. |
| `cyborg_hvac.dismiss_recommendation` | `recommendation`, `minutes` (default 240, up to 10080), `unit_id`, `entry_id` | Hides a recommendation for a while. |
| `cyborg_hvac.pause_autopilot` | `minutes` (default 120, up to 1440), `unit_id`, `entry_id` | Holds every autopilot behaviour and zone. Without a unit, every unit. |
| `cyborg_hvac.resume_autopilot` | `unit_id`, `entry_id` | Ends pauses and manual holds early. |

`unit_id` and `entry_id` are only needed with more than one unit or integration entry.

## Dashboard card

The **HVAC card** in [Cyborg Cards](https://github.com/engabd11/cyborg-cards) (`custom:cyborg-hvac-card`, version 1.5.0 or newer for everything here) shows the unit, every zone with its role and health, advice with Apply and Dismiss, the Butler controls with the energy setting, and the **Climate Butler** panel: what it is doing now and why, the rooms it is watching with their trend and when it steps in, what is coming up, and its journal with a week of history.

The card reads one live feed per unit over the websocket instead of rebuilding the logic in the browser, so any card or tool can use it. See the [card contract](https://github.com/engabd11/cyborg-hvac/blob/main/docs/card-contract.md).

## Automation examples

Bedtime at 10 pm, off in the morning (before 0.7 the switch was `switch.<unit>_autopilot_bedtime`; an existing install keeps that id):

```yaml
automation:
  - alias: Bedtime on
    triggers:
      - trigger: time
        at: "22:00:00"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.duct_air_conditioner_bedtime
  - alias: Bedtime off
    triggers:
      - trigger: time
        at: "06:30:00"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.duct_air_conditioner_bedtime
```

Keep the battery for the night on a cloudy day:

```yaml
automation:
  - alias: Energy on Eco when the battery is low
    triggers:
      - trigger: numeric_state
        entity_id: sensor.house_battery
        below: 40
    actions:
      - action: select.select_option
        target:
          entity_id: select.duct_air_conditioner_energy
        data:
          option: eco
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

- **The Butler is not changing a zone or the unit.** Someone changed it in the last 2 hours (the card shows a hand, the journal says until when, and the switches' `zones_paused` and `paused_until` attributes too). Press **Resume**, or switch the Climate Butler off and on. Also check the window and door sensors: an open zone is left closed.
- **It will not switch from heating to cooling.** Look at the card's Watching list: a room left to cool on its own shows how fast it is falling and when the Butler steps in if it has not. Otherwise the unit changed between heating and cooling recently (the reason says when it may again), or it is the season against it and the room is not far enough past its range for the energy mode (Max and Bedtime need little or nothing). Without an outdoor sensor or forecast, it needs a larger difference before switching.
- **A room is closed even though it is only a bit warm.** It is ahead of the others while they still need heating, and rejoins once it is level with them (role `shed`).
- **Why did it do that?** The [journal](#journal) on the card has every change with its reason, and the Climate Butler activity sensor's history shows what it was doing when. [Diagnostics](https://www.home-assistant.io/docs/configuration/troubleshooting/#download-diagnostics) include the Butler's state and decision, the energy mode, each room's trend, what it has learned and the latest journal, and the unit's raw status, with identifiers removed.
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
