"""Read-only adapters between outside data providers and the AdvisorOS domain model.

Every module here is subject to one invariant, asserted by `tests/security`: nothing in this
package may place, cancel, or preview an order. Providers offer trading; AdvisorOS does not
consume it. See `base.py` for why that is enforced by absence from the dependency tree rather
than by a policy of not calling it.
"""
