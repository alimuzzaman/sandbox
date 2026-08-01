from .file_fragment import FileFragmentAdapter


def render_nginx(route_id, hostname, address, port, listen, wildcard=False):
    listen_address = listen["address"]
    if ":" in listen_address:
        listen_address = f"[{listen_address}]"
    backend_address = f"[{address}]" if ":" in address else address
    listen_port = int(listen["port"])
    server_names = f"{hostname} *.{hostname}" if wildcard else hostname
    return (
        f"# sandbox-ingress v1 route={route_id}\n"
        "server {\n"
        f"    listen {listen_address}:{listen_port};\n"
        f"    server_name {server_names};\n"
        "    location / {\n"
        f"        proxy_pass http://{backend_address}:{port};\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Forwarded-Proto $scheme;\n"
        "    }\n}\n"
    )


class NginxAdapter(FileFragmentAdapter):
    adapter_id = "system-nginx"
    def __init__(self, **kwargs): super().__init__(render=render_nginx, **kwargs)
