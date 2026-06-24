"""Bella Vista reservation agent package.

Layering (top depends on bottom; bottom knows nothing of the top):

    app.py / FastAPI            <- HTTP + static UI
    agent.py                    <- conversation orchestration (LLM or deterministic)
    nlu.py / llm.py             <- two interchangeable "brains"
    tools.py                    <- the 5 agent capabilities + ALL business rules
    store.py / allocator.py     <- persistence + table-seating feasibility
    models.py / config.py / clock.py

All correctness lives at the `tools`/`store`/`allocator` level, so it is fully
unit-testable without any LLM in the loop.
"""
