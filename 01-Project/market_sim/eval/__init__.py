"""LLM behavioral-rationality evaluation (task 2).

L1 (interface) + L2 (8 probe scenarios) + scorecard, run against a real model via
Vertex AI (Gemini). The harness reuses the engine and the agent API; it does NOT
run live multi-round market simulations (that L3 stage is out of scope for now).
"""
