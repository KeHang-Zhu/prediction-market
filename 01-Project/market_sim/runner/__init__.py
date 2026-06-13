"""Runner layer: configuration, event sourcing, sinks, and the round loop.

The runner owns the single source of randomness (one numpy Generator) and is the
only place that touches the wall clock (event timestamps, masked on replay).
"""
