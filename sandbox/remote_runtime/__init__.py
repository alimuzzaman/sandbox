"""Remote runtime compatibility (spec 061).

One rule decides whether a local controller may operate an installed remote
runtime: the declared control-protocol range in :mod:`.protocol`, evaluated by
:func:`.verdict.compatibility`. Refusals share one shape (:mod:`.refusal`).
Strict mode keeps exact-revision matching and registers a visible pin
(:mod:`.pins`).
"""
