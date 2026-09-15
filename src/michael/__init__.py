"""Michael — an internal legal research and drafting assistant.

Single user, not published, not multi-tenant. Optimised for correctness and
traceability, not for scale.

Everything in this package is a *tool*. The orchestrator (Hermes) plans, calls
these tools, and writes the answer. There is deliberately no second agent
framework here.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
