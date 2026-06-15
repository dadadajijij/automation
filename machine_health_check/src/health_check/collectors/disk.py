from __future__ import annotations

import os


def collect_disks(mount_points: list[str]) -> list[dict[str, float | int | str]]:
    disks: list[dict[str, float | int | str]] = []

    for mount_point in mount_points:
        stats = os.statvfs(mount_point)
        total_blocks = stats.f_blocks * stats.f_frsize
        available_blocks = stats.f_bavail * stats.f_frsize
        used_blocks = total_blocks - (stats.f_bfree * stats.f_frsize)
        usage_percent = round((used_blocks / total_blocks) * 100, 2) if total_blocks else 0.0

        total_inodes = stats.f_files
        free_inodes = stats.f_ffree
        used_inodes = max(total_inodes - free_inodes, 0)
        inode_usage_percent = round((used_inodes / total_inodes) * 100, 2) if total_inodes else 0.0

        disks.append(
            {
                "mount_point": mount_point,
                "total_bytes": total_blocks,
                "available_bytes": available_blocks,
                "used_bytes": used_blocks,
                "usage_percent": usage_percent,
                "total_inodes": total_inodes,
                "used_inodes": used_inodes,
                "inode_usage_percent": inode_usage_percent,
            }
        )

    return disks
