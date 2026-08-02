from .file_fragment import FileFragmentAdapter


def render_caddy(route_id, hostname, address, port, listen, wildcard=False):
    if wildcard:
        raise ValueError("system Caddy wildcard adoption is not proven")
    names = f"{hostname}, *.{hostname}" if wildcard else hostname
    backend_address = f"[{address}]" if ":" in address else address
    listen_address = str(listen["address"])
    if not listen.get("loopback_clients_only"):
        return (f"# sandbox-ingress v1 route={route_id}\n"
                f"http://{names} {{\n    bind {listen_address}\n"
                f"    reverse_proxy {backend_address}:{port}\n}}\n")
    # The incumbent listens on a wildcard, so binding its socket would otherwise
    # publish this instance to every interface. Serve loopback clients only and
    # refuse everything else, so adoption never widens reachability.
    return (f"# sandbox-ingress v1 route={route_id}\n"
            f"http://{names} {{\n    bind {listen_address}\n"
            f"    @loopback remote_ip 127.0.0.0/8 ::1\n"
            f"    handle @loopback {{\n"
            f"        reverse_proxy {backend_address}:{port}\n    }}\n"
            f"    handle {{\n        respond 403\n    }}\n}}\n")


class CaddyAdapter(FileFragmentAdapter):
    adapter_id = "system-caddy"
    extension = "caddy"
    def __init__(self, **kwargs): super().__init__(render=render_caddy, **kwargs)
