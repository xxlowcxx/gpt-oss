#!/usr/bin/env python3
"""Utility helpers for staging Felony's Big Booter USB maintenance drive.

The script focuses on predictable filesystem layout instead of trying to
re-invent a complete Ventoy-like solution.  It automates the repetitive
steps required to prepare a GRUB based menu that exposes ISO/WIM/IMG files
copied into a USB drive and adds optional maintenance hooks.

Key design points
=================
- Works with both BIOS and UEFI firmware by using GRUB 2 as the primary
  bootloader.  When running the automated provisioning flow with
  `--prepare-device --embed-grub` the script drops a ready-to-boot EFI
  binary onto the USB stick without needing third-party flashing tools.
- ISO, IMG and WIM files dropped into dedicated folders are detected
  automatically and a `grub.cfg` is rendered with boot menu entries.
- EFI binaries placed in the `efi-tools` folder appear as firmware
  utilities allowing quick access to vendor BIOS updaters.
- A maintenance entry runs a lightweight BusyBox based environment (if the
  user copies one into the `maintenance` folder) and exposes helper scripts
  for running `fwupdmgr`, `dislocker`, `chntpw` or similar tools that are
  commonly used to repair operating systems.

By default the script is declarative and only touches files within the staging
directory.  When the optional `--prepare-device` flag is provided it can also
partition and format a USB device automatically.  That workflow requires root
privileges and trusted tooling such as `sgdisk`, `mkfs.fat`, `mkfs.exfat`, etc.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from textwrap import dedent
from typing import Dict, List, Mapping, Sequence

PROJECT_NAME = "Felony's Big Booter"
PROJECT_TAGLINE = "the all-in-one upload, update, repair app by T00L-A1D powered by Felony Logix"


SUPPORTED_IMAGE_EXTENSIONS = {
    ".iso": "ISO installer or live image",
    ".img": "Disk image",
    ".wim": "Windows Imaging Format",
}

SUPPORTED_EFI_EXTENSIONS = {".efi"}

CONFIG_FILENAME = "boot/grub/grub.cfg"
CATALOG_FILENAME = "boot/catalog.json"
HELPER_SCRIPT = "maintenance/run-maintenance.sh"
EFI_BOOT_PATH = Path("EFI/BOOT/BOOTX64.EFI")
GRUB_STANDALONE_TARGET = "x86_64-efi"
DEFAULT_ESP_SIZE_MIB = 512
DATA_PARTITION_LABEL = "MULTIBOOT"
ESP_PARTITION_LABEL = "MULTIBOOT_ESP"


def _rel_path(base: Path, target: Path) -> str:
    """Return POSIX relative path."""

    return target.relative_to(base).as_posix()


def discover_images(base_dir: Path) -> Dict[str, List[Mapping[str, str]]]:
    """Return mapping of image categories to discovered entries."""

    categories: Dict[str, List[Mapping[str, str]]] = {
        "installers": [],
        "firmware": [],
        "maintenance": [],
    }

    image_root = base_dir / "images"
    firmware_root = base_dir / "efi-tools"
    maintenance_root = base_dir / "maintenance"

    for folder, bucket in [
        (image_root, "installers"),
        (maintenance_root, "maintenance"),
    ]:
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                categories[bucket].append(
                    {
                        "name": path.stem,
                        "path": _rel_path(base_dir, path),
                        "description": SUPPORTED_IMAGE_EXTENSIONS[path.suffix.lower()],
                    }
                )

    if firmware_root.is_dir():
        for path in sorted(firmware_root.rglob("*.efi")):
            if path.is_file() and path.suffix.lower() in SUPPORTED_EFI_EXTENSIONS:
                categories["firmware"].append(
                    {
                        "name": path.stem,
                        "path": _rel_path(base_dir, path),
                        "description": "UEFI firmware utility",
                    }
                )

    return categories


def render_catalog(categories: Mapping[str, Sequence[Mapping[str, str]]]) -> str:
    return json.dumps(categories, indent=2, sort_keys=True)


def render_grub_config(categories: Mapping[str, Sequence[Mapping[str, str]]]) -> str:
    """Generate a GRUB 2 configuration that exposes installers and tools."""

    header = dedent(
        f"""
        set timeout=15
        set default=0
        set color_normal=light-cyan/black
        set color_highlight=white/light-blue

        if loadfont unicode ; then
          set gfxmode=1024x768,auto
          set gfxpayload=keep
          insmod gfxterm
          terminal_output gfxterm
        fi

        menuentry "{PROJECT_NAME} status" --class info {{
          echo "{PROJECT_NAME}"
          echo "{PROJECT_TAGLINE}"
          echo ''
          echo "Drop ISO/IMG/WIM installers into /images."
          echo "Firmware .efi tools belong in /efi-tools."
          echo "Maintenance payloads live in /maintenance."
          echo ''
          echo "Linux ISOs with loopback kernels work best."
          echo "Windows WIMs require boot/tools/wimboot."
          echo "macOS/iOS media cannot boot from generic GRUB."
          echo "Use vendor firmware tools for BIOS updates."
          echo "Maintenance entry depends on your payloads."
          sleep 4
        }}
        """
    ).strip()

    menu_entries: List[str] = [header]

    def _make_loopback_entry(item: Mapping[str, str], idx: int) -> str:
        return dedent(
            f"""
            menuentry '{item['name']}' --class os {{
              set iso_path='({{root}})/{item['path']}'
              echo 'Booting {item['name']} from $iso_path'
              loopback loop $iso_path
              linux (loop)/casper/vmlinuz boot=casper iso-scan/filename=$iso_path quiet splash ---
              initrd (loop)/casper/initrd
            }}
            """
        ).strip()

    def _make_wim_entry(item: Mapping[str, str], idx: int) -> str:
        return dedent(
            f"""
            menuentry '{item['name']} (WIM)' --class windows {{
              set wim_path='({{root}})/{item['path']}'
              echo 'Booting {item['name']} using wimboot shim'
              insmod ntldr
              insmod part_gpt
              insmod ntfs
              set bootmgr='({{root}})/boot/tools/wimboot'
              if [ -f $bootmgr ]; then
                linux $bootmgr @wimboot
                initrd $wim_path
              else
                echo 'Missing boot/tools/wimboot. Please copy the binary from ipxe.org/wimboot.'
                sleep 3
              fi
            }}
            """
        ).strip()

    def _make_firmware_entry(item: Mapping[str, str], idx: int) -> str:
        return dedent(
            f"""
            menuentry '{item['name']} (UEFI tool)' --class settings {{
              chainloader '({{root}})/{item['path']}'
            }}
            """
        ).strip()

    for idx, item in enumerate(categories.get("installers", [])):
        suffix = Path(item["path"]).suffix.lower()
        if suffix == ".wim":
            menu_entries.append(_make_wim_entry(item, idx))
        else:
            menu_entries.append(_make_loopback_entry(item, idx))

    if categories.get("firmware"):
        menu_entries.append("submenu 'Firmware utilities' {")
        for idx, item in enumerate(categories["firmware"]):
            menu_entries.append(_make_firmware_entry(item, idx))
        menu_entries.append("}")

    if categories.get("maintenance"):
        menu_entries.append("submenu 'Maintenance environments' {")
        for idx, item in enumerate(categories["maintenance"]):
            menu_entries.append(_make_loopback_entry(item, idx))
        menu_entries.append("}")

    menu_entries.append(
        dedent(
            f"""
            menuentry 'Launch maintenance shell' --class recovery {{
              if [ -f '({{root}})/{HELPER_SCRIPT}' ]; then
                echo 'Starting maintenance helper'
                linux '({{root}})/{HELPER_SCRIPT}'
              else
                echo 'No maintenance helper found at {HELPER_SCRIPT}.\nUse --install-maintenance to create one.'
                sleep 3
              fi
            }}
            """
        ).strip()
    )

    return "\n\n".join(menu_entries) + "\n"


def ensure_structure(base_dir: Path) -> None:
    """Create the expected folder layout."""

    for relative in [
        "boot/grub",
        "boot/tools",
        "images",
        "maintenance",
        "efi-tools",
    ]:
        (base_dir / relative).mkdir(parents=True, exist_ok=True)


MAINTENANCE_SCRIPT = dedent(
    """#!/bin/sh
set -eu

cat <<'MSG'
This placeholder script documents how to hook in your maintenance tools.
Replace it with a BusyBox based Linux kernel + initrd or a WinPE build for
advanced recovery workflows.

Suggested toolkit additions (copy their binaries into `maintenance/tools`):
  * fwupd / fwupdmgr for cross-vendor firmware updates
  * dislocker or libbde to unlock BitLocker volumes
  * chntpw to reset Windows passwords
  * testdisk + photorec for data recovery
  * rsync + sshfs for backups
MSG
sleep 5
"""
)


def install_maintenance_helper(base_dir: Path) -> None:
    helper_path = base_dir / HELPER_SCRIPT
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    helper_path.write_text(MAINTENANCE_SCRIPT)
    helper_path.chmod(0o755)


def _warn(message: str) -> None:
    print(f"[!] {message}")


def _info(message: str) -> None:
    print(f"[+] {message}")


def _run_command(args: Sequence[str], *, cwd: Path | None = None) -> None:
    _info("Running: " + " ".join(args))
    subprocess.run(args, cwd=cwd, check=True)


def _optional_run(args: Sequence[str]) -> None:
    try:
        _run_command(args)
    except (subprocess.CalledProcessError, FileNotFoundError):
        _warn(f"Optional step failed or tool missing for: {' '.join(args)}")


def _require_tool(tool_names: Sequence[str]) -> str:
    for tool in tool_names:
        path = shutil.which(tool)
        if path:
            return tool
    raise FileNotFoundError(f"Required tools not found: {', '.join(tool_names)}")


def _partition_path(device: Path, number: int) -> Path:
    name = device.name
    if name.startswith("nvme") or name.startswith("mmcblk") or name.startswith("loop"):
        suffix = f"p{number}"
    else:
        suffix = str(number)
    return device.parent / f"{name}{suffix}"


def _udev_settle() -> None:
    tool = shutil.which("udevadm")
    if tool:
        _optional_run([tool, "settle"])
    time.sleep(1)


def prepare_usb_device(
    device: Path,
    *,
    mount_point: Path,
    esp_size_mib: int = DEFAULT_ESP_SIZE_MIB,
    data_filesystem: str = "exfat",
) -> None:
    """Partition, format and mount a USB drive automatically."""

    if os.name != "posix":
        raise RuntimeError("Automatic device preparation is only supported on POSIX systems.")
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise PermissionError("Preparing a USB device requires root privileges.")

    device = device.resolve()
    if not device.exists():
        raise FileNotFoundError(f"Block device {device} does not exist.")

    mount_point.mkdir(parents=True, exist_ok=True)

    # Attempt to unmount existing partitions before repartitioning.
    lsblk = shutil.which("lsblk")
    if lsblk:
        result = subprocess.run(
            [lsblk, "-ln", "-o", "PATH", str(device)],
            check=True,
            capture_output=True,
            text=True,
        )
        existing = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        for path in existing:
            if path != str(device):
                try:
                    _run_command(["umount", path])
                except subprocess.CalledProcessError:
                    _warn(f"Could not unmount {path}. Ensure no files are in use before retrying.")

    sgdisk = _require_tool(["sgdisk"])
    fat_mkfs = _require_tool(["mkfs.fat", "mkfs.vfat"])

    data_mkfs_map = {
        "exfat": ("mkfs.exfat", ["mkfs.exfat", "-n", DATA_PARTITION_LABEL]),
        "ntfs": ("mkfs.ntfs", ["mkfs.ntfs", "-F", "-L", DATA_PARTITION_LABEL]),
        "ext4": ("mkfs.ext4", ["mkfs.ext4", "-F", "-L", DATA_PARTITION_LABEL]),
    }
    if data_filesystem not in data_mkfs_map:
        raise ValueError(f"Unsupported filesystem '{data_filesystem}'.")
    tool_name, base_cmd = data_mkfs_map[data_filesystem]
    data_tool = _require_tool([tool_name])
    base_cmd[0] = data_tool

    # Zap existing partition table and create new GPT layout.
    _run_command([sgdisk, "--zap-all", str(device)])
    _run_command([sgdisk, "-og", str(device)])
    _run_command(
        [
            sgdisk,
            "-n",
            "1:2048:+{}M".format(int(esp_size_mib)),
            "-t",
            "1:ef00",
            "-c",
            f"1:{ESP_PARTITION_LABEL}",
            str(device),
        ]
    )
    _run_command([sgdisk, "-n", "2:0:0", "-t", "2:0700", "-c", f"2:{DATA_PARTITION_LABEL}", str(device)])

    _optional_run(["partprobe", str(device)])
    _udev_settle()

    esp_partition = _partition_path(device, 1)
    data_partition = _partition_path(device, 2)

    _run_command([fat_mkfs, "-F", "32", "-n", ESP_PARTITION_LABEL, str(esp_partition)])

    mkfs_cmd = list(base_cmd) + [str(data_partition)]
    _run_command(mkfs_cmd)

    _udev_settle()

    mountpoint_tool = shutil.which("mountpoint")
    if mountpoint_tool:
        result = subprocess.run(
            [mountpoint_tool, "-q", str(mount_point)],
            capture_output=True,
        )
        if result.returncode == 0:
            _optional_run(["umount", str(mount_point)])

    # Mount the data partition so that staging can continue automatically.
    try:
        _run_command(["mount", str(data_partition), str(mount_point)])
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to mount {data_partition} at {mount_point}. Ensure the mount point is free and try again."
        ) from exc

    _info(
        "USB device prepared with a {} ESP and {} data partition. Mounted data partition at {}.".format(
            f"{esp_size_mib} MiB",
            data_filesystem.upper(),
            mount_point,
        )
    )



class MultiBootUSB:
    def __init__(self, root: Path):
        self.root = root

    def build(self, install_helper: bool = False, embed_grub: bool = False) -> None:
        ensure_structure(self.root)
        categories = discover_images(self.root)

        grub_cfg = render_grub_config(categories)
        catalog = render_catalog(categories)

        grub_path = self.root / CONFIG_FILENAME
        grub_path.write_text(grub_cfg)
        catalog_path = self.root / CATALOG_FILENAME
        catalog_path.write_text(catalog)

        _info(
            f"{PROJECT_NAME} menu rendered with {len(categories.get('installers', []))} installers, "
            f"{len(categories.get('firmware', []))} firmware utilities and "
            f"{len(categories.get('maintenance', []))} maintenance environments."
        )
        _info(f"Configuration written to {grub_path} and catalog saved to {catalog_path}.")

        if install_helper:
            install_maintenance_helper(self.root)
            _info("Installed maintenance helper script for quick customisation.")

        if embed_grub:
            self.embed_grub(grub_path)

    def embed_grub(self, config_path: Path) -> None:
        """Generate a standalone GRUB EFI binary using grub-mkstandalone."""

        tool = shutil.which("grub-mkstandalone")
        if not tool:
            _warn(
                "grub-mkstandalone not found. Install grub-efi-amd64-bin or grub2-efi packages to embed GRUB automatically.",
            )
            return

        efi_target = self.root / EFI_BOOT_PATH
        efi_target.parent.mkdir(parents=True, exist_ok=True)

        _info(
            "Embedding GRUB EFI binary. The resulting USB should boot on most UEFI systems without additional steps.",
        )

        try:
            _run_command(
                [
                    tool,
                    "-O",
                    GRUB_STANDALONE_TARGET,
                    "-o",
                    str(efi_target),
                    f"boot/grub/grub.cfg={config_path}",
                ]
            )
        except subprocess.CalledProcessError:
            _warn(
                "Failed to embed GRUB automatically. Review the output above and install GRUB manually if required.",
            )
            return
        _info(
            "GRUB EFI binary created at EFI/BOOT/BOOTX64.EFI. Copy the entire directory structure to your USB drive.",
        )

    @classmethod
    def from_args(cls, argv: Sequence[str] | None = None) -> "MultiBootUSB":
        parser = argparse.ArgumentParser(
            description=(
                f"Generate GRUB menu and metadata for {PROJECT_NAME}, "
                "the all-in-one upload, update, repair helper."
            ),
        )
        parser.add_argument(
            "root",
            nargs="?",
            type=Path,
            help="Path to the USB mount point or staging directory",
        )
        parser.add_argument(
            "--install-maintenance",
            action="store_true",
            help="Drop a sample maintenance helper script that the user can customise.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Discover entries and print the generated GRUB configuration without writing files.",
        )
        parser.add_argument(
            "--stdout",
            action="store_true",
            help="Print generated files to stdout for debugging.",
        )
        parser.add_argument(
            "--embed-grub",
            action="store_true",
            help="Attempt to build a standalone GRUB EFI binary using grub-mkstandalone.",
        )
        parser.add_argument(
            "--prepare-device",
            action="store_true",
            help="Partition, format and mount the USB device automatically (requires root).",
        )
        parser.add_argument(
            "--device",
            type=Path,
            help="Raw USB block device to prepare, for example /dev/sdX or /dev/nvme1n1.",
        )
        parser.add_argument(
            "--mount-point",
            type=Path,
            help="Where to mount the prepared data partition when using --prepare-device.",
        )
        parser.add_argument(
            "--data-filesystem",
            choices=["exfat", "ntfs", "ext4"],
            default="exfat",
            help="Filesystem to use for the large data partition when preparing a device.",
        )
        parser.add_argument(
            "--esp-size-mib",
            type=int,
            default=DEFAULT_ESP_SIZE_MIB,
            help="Size of the EFI System Partition created by --prepare-device (in MiB).",
        )

        args = parser.parse_args(argv)
        root_path: Path | None = args.root

        if args.prepare_device:
            if not args.device or not args.mount_point:
                parser.error("--prepare-device requires both --device and --mount-point arguments.")
            try:
                prepare_usb_device(
                    args.device,
                    mount_point=args.mount_point,
                    esp_size_mib=args.esp_size_mib,
                    data_filesystem=args.data_filesystem,
                )
            except (PermissionError, FileNotFoundError, RuntimeError, ValueError) as error:
                parser.error(str(error))
            except subprocess.CalledProcessError as error:
                parser.error(f"Failed to prepare device: {error}")

            if root_path is None:
                root_path = args.mount_point

        if root_path is None:
            parser.error("A staging root must be provided as a positional argument or via --mount-point.")

        instance = cls(root_path)

        ensure_structure(instance.root)
        categories = discover_images(instance.root)

        if args.dry_run:
            if args.stdout:
                print(render_grub_config(categories))
                print("\n== catalog.json ==\n")
                print(render_catalog(categories))
            return instance

        instance.build(
            install_helper=args.install_maintenance,
            embed_grub=args.embed_grub,
        )

        if args.stdout:
            print(instance.root / CONFIG_FILENAME)
            print(render_grub_config(categories))
            print("\n" + str(instance.root / CATALOG_FILENAME))
            print(render_catalog(categories))

        return instance


if __name__ == "__main__":
    MultiBootUSB.from_args()
