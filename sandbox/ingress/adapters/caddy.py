from .file_fragment import FileFragmentAdapter


def render_caddy(route_id, hostname, address, port, listen, wildcard=False):
    names = f"{hostname}, *.{hostname}" if wildcard else hostname
    backend_address = f"[{address}]" if ":" in address else address
    return (f"# sandbox-ingress v1 route={route_id}\n"
            f"http://{names} {{\n    reverse_proxy {backend_address}:{port}\n}}\n")


class CaddyAdapter(FileFragmentAdapter):
    adapter_id = "system-caddy"
    extension = "caddy"
    def __init__(self, **kwargs): super().__init__(render=render_caddy, **kwargs)
