"""Tailnet-only HTTPS hostnames on a remote (spec 078, ``sb host tailnet-name``).

Ownership model
---------------
On the remote, Sandbox writes only its own nginx route file
``/etc/nginx/conf.d/sandbox-tailnet-<fqdn>.conf`` (through the nginx front
door's route transaction) and the certificate directory
``/etc/nginx/sandbox-tailnet/<fqdn>/``. It never edits the incumbent's files
and never declares ``default_server``. In the DNS zone it creates, adopts or
deletes only the one exact-name A record it recorded.

Secrets
-------
The Cloudflare token reaches only two fixed child processes run by the secrets
broker: the shipped ``cloudflare_child`` program and ``lego``. It is never in
argv, configuration, state, output or logs. The certificate's private key is
generated on the remote and never leaves it.
"""
