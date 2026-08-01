from .file_fragment import FileFragmentAdapter


def render_traefik(route_id, hostname, address, port, listen, wildcard=False):
    rule = f"Host(`{hostname}`)"
    if wildcard:
        rule += f" || HostRegexp(`{{subdomain:.+}}.{hostname}`)"
    key = f"sandbox-{route_id[:20]}"
    backend_address = f"[{address}]" if ":" in address else address
    return (
        f"# sandbox-ingress v1 route={route_id}\nhttp:\n  routers:\n"
        f"    {key}:\n      rule: \"{rule}\"\n      service: {key}\n"
        f"  services:\n    {key}:\n      loadBalancer:\n        servers:\n"
        f"          - url: \"http://{backend_address}:{port}\"\n"
    )


class TraefikAdapter(FileFragmentAdapter):
    adapter_id = "traefik"
    extension = "yml"
    def __init__(self, **kwargs): super().__init__(render=render_traefik, **kwargs)
