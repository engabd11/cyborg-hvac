"""Brand-agnostic core of Cyborg HVAC.

Nothing in this package imports Home Assistant. Entities, the coordinator and
the logic engines only ever see the types defined here, and every brand backend
translates its own protocol to and from them.
"""
