"""Controller auto-update (spec 072).

A remote-capable ``sb`` command fast-forwards its own checkout to a newer
Sandbox revision that a remote reports, but only after verifying that the
revision is on the published branch the checkout follows on its GitHub
``origin``. See ``docs/controller-auto-update.md``.
"""
