from .file_fragment import FileFragmentAdapter


def render_apache(route_id, hostname, address, port, listen, wildcard=False):
    backend_address = f"[{address}]" if ":" in address else address
    alias = f"\n    ServerAlias *.{hostname}" if wildcard else ""
    return (
        f"# sandbox-ingress v1 route={route_id}\n"
        f"<VirtualHost {listen['address']}:{int(listen['port'])}>\n"
        f"    ServerName {hostname}{alias}\n"
        "    ProxyPreserveHost On\n"
        f"    ProxyPass / http://{backend_address}:{port}/\n"
        f"    ProxyPassReverse / http://{backend_address}:{port}/\n"
        "</VirtualHost>\n"
    )


class ApacheAdapter(FileFragmentAdapter):
    adapter_id = "system-apache"
    def __init__(self, **kwargs): super().__init__(render=render_apache, **kwargs)
