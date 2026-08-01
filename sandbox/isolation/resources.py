"""Compile bounded cgroup, rlimit, runtime, and image resource controls."""

from __future__ import annotations


class ResourcePolicyCompiler:
    def compile(self, values):
        defaults = {"cpu_percent": 200, "memory_bytes": 2 * 1024**3,
                    "pids": 512, "runtime_seconds": 3600, "disk_bytes": 8 * 1024**3,
                    "inodes": 500000, "fds": 4096, "connections": 512,
                    "io_weight": 100}
        policy = {**defaults, **dict(values)}
        ranges = {"cpu_percent": (10, 6400), "memory_bytes": (128 * 1024**2, 256 * 1024**3),
                  "pids": (32, 65536), "runtime_seconds": (1, 86400),
                  "disk_bytes": (1024**3, 1024**4), "inodes": (10000, 10000000),
                  "fds": (128, 1048576), "connections": (16, 65535), "io_weight": (1, 10000)}
        for key, (low, high) in ranges.items():
            value = policy[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"managed resource {key} is out of bounds")
        return {**policy, "systemd": {
            "CPUQuota": f"{policy['cpu_percent']}%", "MemoryMax": policy["memory_bytes"],
            "MemorySwapMax": 0, "TasksMax": policy["pids"],
            "RuntimeMaxSec": policy["runtime_seconds"], "LimitNOFILE": policy["fds"],
            "IOWeight": policy["io_weight"],
        }}
