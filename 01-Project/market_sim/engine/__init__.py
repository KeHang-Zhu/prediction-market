"""Pure engine layer: no I/O, no randomness, no wall-clock in logic.

All randomness is injected by the runner. The engine is a deterministic
function of (initial state, ordered action stream).
"""
