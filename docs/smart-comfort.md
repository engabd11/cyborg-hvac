# Smart comfort

Cyborg HVAC can run your ducted system the way a careful owner would: the right mode for the weather, air only where people are, every room held in its comfort range, rooms kept healthy, and work moved to when it is cheapest. It always explains itself first, and only acts on its own where you switch that on.

## Set it up

1. **House sensors and weather** (integration page, **Configure**): an outdoor temperature sensor, a weather forecast (the BOM integration works well), the people who live there, and a grid power sensor so spare solar can be spotted.
2. **Comfort targets** (same place): heating and cooling targets for day, night and away, the highest comfortable humidity, and when night starts and ends. Below the heating target a room is too cold; above the cooling target it is too warm; in between is comfortable.
3. **Each zone** (the zone's settings): its room temperature and humidity sensors, presence or motion sensors and how long they may stay quiet before the room counts as empty (presence hold, 15 minutes by default), window sensors, door sensors, a comfort offset (for example a bedroom 1 degree cooler), its room type, whether it is a guest zone or a sleep zone, opening limits, and whether it is a spill zone. Mark the zone your installer set as spill on the controller (on the HACZ01 it is in the installer menu).

Anything you leave out simply switches off the advice that needs it. An outdoor sensor or forecast matters most: without one, autopilot cannot tell whether outside will cool or warm a room by itself, so it never switches between heating and cooling on a small difference.

## Advice

The **Advice** sensor and the HVAC card list what would help right now, most urgent first. Examples:

- Heating while rooms are already too warm, or cooling while they are cold.
- A room well ahead of the others while they heat or cool: close it while they catch up.
- Rooms warm but outside cold: no need to cool, they come back on their own (with how long it takes).
- Rooms comfortable and outside mild: turn off and let the house coast.
- Outside cooler than inside on a summer evening: open windows instead.
- Rooms uneven but fine on average: run the fan to mix them (not while it is humid: the fan would put moisture from the coil back into the rooms).
- Humid rooms: dry mode.
- **Health**: a room at risk of mould or condensation, a bedroom above 26 °C at night, an occupied room above 28 °C, or a room below a healthy minimum.
- A heating setpoint of 26 or more, or cooling of 21 or less: each degree costs about 10% more and does not heat or cool faster.
- Too few zones open, an empty room left open, a window open while its zone runs, or someone in a closed zone.
- Everyone out: set back for a short trip, turn off for a long one (or keep the setback while a heatwave or cold snap is coming).
- A heatwave, hot day or cold night in the forecast: what to do, and pre-cooling or pre-heating while it is cheaper.
- Spare solar: bank some comfort now.
- The unit asks for its filter to be cleaned.

Apply a recommendation from the card or with `cyborg_hvac.apply_recommendation`; dismiss it for a while with `cyborg_hvac.dismiss_recommendation`.

## How autopilot decides

With **Room balancing** or **Bedtime** on, one engine runs the unit and every zone together. The other switches feed it: presence, the forecast, spare solar, guest mode and health change what each room should reach, and the engine works out the gentlest way to get there. It always prefers, in this order:

1. **Let it coast.** A room past its aim that outside will bring back on its own (cooling down on a cold night, warming up on a mild day) is closed and left to drift. Autopilot predicts how long that takes from what it has learned about the room and the forecast, and checks the prediction comes true. It does not switch heating to cooling for it.
2. **Move a damper** when rooms differ from each other: a room behind the others gets more air, a room ahead gets less, and a room well ahead (1.5 degrees past its aim) is closed until it is level with the rest of the house, then rejoins.
3. **Move the setpoint** when the house as a whole is off. The unit only measures return air, so the setpoint is corrected for how far that reads from the rooms.
4. **Change mode** only when nothing gentler works: a room has stayed too warm (or too cold) for 20 minutes, outside will not fix it, the unit has been in its mode for an hour, and in the heating season a room has to be well past its range before cooling is used (and the other way round).
5. **Power**: when every room will stay comfortable for a while, the unit rests (switches off) rather than idling.

Changes are few and decisive. A damper goes straight to where it should be (100% to 60% in one move, not four) and then stays put for at least 15 minutes; moves under 20% wait. The setpoint moves by a degree or more (half a degree at bedtime), at most every 15 minutes, except to ease off once rooms overshoot. The fan changes at most every half hour. Everything goes out as one command.

The card shows what autopilot is doing and why, for example "Heating MASTER (19.5 °C). Letting LIVING (24 °C) cool on its own (8 °C outside).", and what each zone is doing: **serve** (needs air), **hold** (at its aim), **shed** (ahead, closed until level), **setback** (empty or second), **keeper** (open for the fan), **shut** (window open), **held** (a person's change).

**Fan**: auto normally; low when cooling humid air (it dries better) and at bedtime (quieter); high in a heatwave's hottest hours or when the house is well behind. A fan speed you pick yourself is left alone for 2 hours.

## Health

Autopilot watches every room with a temperature sensor, and with a humidity sensor (or the unit's own humidity reading) it also watches the walls:

- **Mould**: mould grows where surfaces stay at 70% humidity or more for a couple of hours in every twelve. Walls and windows are colder than the room in winter, so their surface humidity is estimated from the room and outdoor temperatures. When the walls have been damp for 2 hours, or are very damp now, it counts as a mould risk.
- **Condensation**: when the coldest surface reaches the dew point.
- **Too hot**: an occupied room above 28 °C, a bedroom above 26 °C at night, an empty room above 30 °C, or an empty house above 32 °C.
- **Too cold**: an occupied room below 16 °C (15 °C at night), or any room below 12 °C.

While any autopilot switch is on, autopilot acts on these even where your settings or presence would not: it cools an overheated bedroom, warms a room that is too cold, gives an empty but damp room enough warmth to dry its walls (never beyond normal comfort), and runs dry mode when humidity stays above your limit outside the heating season. In winter, heating alone cannot remove moisture, so the advice also asks you to air the room.

## Heatwaves and cold snaps

A heatwave is a forecast of 35 °C or more (or 10 degrees above your day cooling target, whichever is higher); a cold snap is a night forecast of 5 °C or less.

In a heatwave, with **Weather pre-conditioning** on, autopilot starts cooling early in the morning while it is mild outside (the unit works far better then) and banks a degree of cool before the heat. During the hottest hours it focuses air on occupied rooms, runs the fan high, and never sets cooling more than 2 degrees below your cooling target: a lower setpoint does not cool faster and only overshoots once the heat breaks. Advice suggests closing blinds and doors.

While everyone is out during a heatwave or cold snap, presence zoning holds the away targets instead of switching off, because a house left to 35 °C or 10 °C takes hours to recover and is hard on anyone coming home.

## Guest mode

Mark the living and dining zones as guest zones. With **Guest mode** on, they get full comfort and priority, and the other zones only need to stay tolerable, so the unit works for the rooms your guests are in.

## Windows and doors

A window counts as open after 2 minutes and a door after 5 (both adjustable in the house settings), so walking through a sliding door does nothing. Once one stays open, the advice suggests closing that zone, and any autopilot that is on closes it and leaves it out of balancing, then puts it back when the window or door shuts. If every open zone is open to outside, the unit pauses until one closes.

## Bedtime

Turn on **Autopilot: bedtime** at bedtime and set **Sleep temperature** (for example 22 °C). Sleep zones are held within half a degree of it whatever the rest of the house does: the unit's mode and setpoint follow the bedroom, and the other zones close down as far as the airflow floor allows (keeping the spill zone as the second air path), with a small trickle of air for any that would get too cold. Sleep zones always count as occupied, and bedtime takes over from room balancing until you switch it off.

If the bedroom is warmer than the sleep temperature on a cold night, bedtime does not cool it: the unit rests (or idles) while outside brings the bedroom down, then holds it there. The fan runs low.

## Airflow floor

While the unit runs, autopilot keeps at least 2 zones open and their openings added together at 150% or more, so the fan always has enough duct. It uses the spill zone first, then zones that are already open (so nothing needs to move), then empty rooms. Both limits are adjustable in the house settings. A spill zone set by your installer already protects the fan; only lower the floor if your installer confirms one zone on its own is safe.

## Autopilot

Five switches, each off until you turn it on (bedtime is described above):

| Behaviour | What it does |
|---|---|
| Room balancing | Hands the unit to the engine: the mode, setpoint, fan and every damper, so every room holds its range and the house stays even. |
| Presence zoning | Rooms empty for 30 minutes (after the presence hold) only need to stay tolerable and get little or no air; rooms people walk into get theirs back. While everyone is out, the house drops to the away targets, and switches off after 2 hours (not during a heatwave or cold snap). |
| Weather pre-conditioning | Uses the hourly forecast and what it has learned about your rooms to start early, so rooms are comfortable when night or morning begins, or before a heat spike. |
| Solar shifting | While you export spare solar, cools or warms up to a degree further within comfort, and stops when the surplus ends. |

Without room balancing or bedtime, presence, pre-conditioning and solar keep a lighter touch: they start, set back and restore the unit, and leave the dampers to you (presence still closes empty rooms).

Autopilot always stays inside guard rails: it never closes the last zones, keeps enough airflow for the fan, waits at least 8 minutes between turning the compressor on and off and 30 minutes between mode changes, spaces setpoint changes, and limits how often it writes to the cloud.

**It respects people.** If someone changes a zone, the unit or its fan in Home Assistant, the ConnectLife app or on the wall controller, autopilot leaves that alone for 2 hours. The controller opening a spill zone by itself does not count. The card marks held zones with a hand. **Resume** (or `cyborg_hvac.resume_autopilot`) ends holds early; **Pause 2 h** (or `cyborg_hvac.pause_autopilot`) holds everything.

## Learning

Cyborg HVAC learns how fast each room loses heat to outside and how quickly the unit warms or cools it. It starts from typical values and becomes confident after a few hours of each, which makes pre-conditioning start at the right time and coasting predictions accurate. It also learns each room's **holding opening**: how much air keeps it level with the rest of the house in heating and in cooling. A room that keeps drifting ahead gets less next time, one that falls behind gets more, so the dampers settle and move less over time. What it learns survives restarts.
