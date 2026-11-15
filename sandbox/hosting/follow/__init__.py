"""Server-side stack branch follower (spec 089).

A follow declares which hosted stack re-applies when which GitHub branches move.
It lives on the remote: ``sb host follow add`` stores it, a systemd user timer runs
``sb host follow tick --here`` every 5 minutes, and each change becomes one durable
server CLI job (``host follow apply``) that runs the declared prepare step and the
normal ``host stack up`` path.
"""
