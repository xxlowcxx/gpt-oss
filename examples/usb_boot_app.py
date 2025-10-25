"""USB boot payload manager for Ventoy-based multi-boot drives.

This script automates copying installation media to a prepared Ventoy USB
drive and generates the supporting configuration so that the payloads can be
booted automatically from BIOS/UEFI firmware.

The tool focuses on the workflow requested in the task description:

* Drop any supported installer/ISO into a staging folder.
* Run this script to synchronise the payloads onto the USB stick.
* Optionally configure the drive to automatically boot a specific payload.

It purposefully avoids invoking privileged operations (such as installing
Ventoy to the USB device).  Instead, it expects the user to prepare the drive
once with the official Ventoy installer.  Afterwards the script keeps the USB
stick updated purely through file copies and JSON configuration updates, which
works on Windows, Linux and macOS.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

SUPPORTED_EXTENSIONS = {
    ".iso",
    ".img",
    ".wim",
    ".vhd",
    ".vhdx",
    ".efi",
    ".zip",
    ".dmg",
    ".gz",
    ".xz",
}

FIRMWARE_KEYWORDS = {
    "bios",
    "firmware",
    "uefi",
    "ec",
    "flash",
}

FORBIDDEN_FIRMWARE_EXTENSIONS = {
    ".bin",
    ".rom",
    ".cap",
    ".fd",
    ".wph",
    ".fl1",
    ".fl2",
}


@dataclass
class Payload:
    """Represents a bootable artefact discovered in the staging directory."""

    source: Path
    relative_destination: Path
    size_bytes: int

    @property
    def display_name(self) -> str:
        return self.relative_destination.name


def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronise installer payloads onto a Ventoy USB drive and generate "
            "autorun configuration so the media can boot directly from BIOS/UEFI."
        )
    )

    parser.add_argument(
        "--payload-dir",
        required=True,
        type=Path,
        help="Directory containing installer media to copy (ISO/WIM/IMG/etc).",
    )
    parser.add_argument(
        "--usb-root",
        required=True,
        type=Path,
        help="Mounted path to the Ventoy USB drive root.",
    )
    parser.add_argument(
        "--default",
        default=None,
        help=(
            "Optional default payload to boot. Specify by 0-based index, "
            "file name, or substring."
        ),
    )
    parser.add_argument(
        "--autorun",
        action="store_true",
        help="Automatically boot the default payload after the timeout expires.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Menu timeout (seconds) before booting the default payload.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the operations that would be performed without copying files.",
    )
    parser.add_argument(
        "--keep-missing",
        action="store_true",
        help="Do not delete payloads from the USB drive that are absent locally.",
    )

    return parser.parse_args(list(argv) if argv is not None else None)


def _discover_payloads(payload_dir: Path) -> List[Path]:
    if not payload_dir.exists():
        raise FileNotFoundError(f"Payload directory not found: {payload_dir}")

    files: List[Path] = []
    for entry in payload_dir.rglob("*"):
        if not entry.is_file():
            continue
        suffix = entry.suffix.lower()
        if suffix in FORBIDDEN_FIRMWARE_EXTENSIONS:
            name = entry.stem.lower()
            if any(keyword in name for keyword in FIRMWARE_KEYWORDS):
                raise ValueError(
                    "Detected potential firmware flashing artefact "
                    f"'{entry.name}'. Firmware updates and BIOS unlocking "
                    "are intentionally unsupported by this helper."
                )
        if suffix in SUPPORTED_EXTENSIONS:
            files.append(entry)
    return sorted(files)


def _build_payload_objects(files: Iterable[Path]) -> List[Payload]:
    payloads: List[Payload] = []
    for file_path in files:
        size = file_path.stat().st_size
        relative = Path("payloads") / file_path.name
        payloads.append(Payload(source=file_path, relative_destination=relative, size_bytes=size))
    return payloads


def _ensure_ventoy_structure(usb_root: Path) -> None:
    """Validate that the target USB folder looks like a Ventoy device."""

    ventoy_dir = usb_root / "ventoy"
    if not ventoy_dir.exists():
        raise FileNotFoundError(
            "The USB root does not contain a 'ventoy' directory. "
            "Install Ventoy on the drive with Ventoy2Disk before running this helper."
        )

    version_file = ventoy_dir / "version"
    if not version_file.exists():
        # The "version" marker is present on official Ventoy installs.  We treat
        # its absence as a warning but allow execution so that advanced users can
        # rely on compatible layouts (e.g. custom Ventoy builds).
        print(
            "Warning: expected Ventoy marker file 'ventoy/version' was not found. "
            "Continuing because 'ventoy/' exists, but double-check that the USB "
            "drive was initialised with Ventoy.",
            file=sys.stderr,
        )


def _copy_payloads(payloads: List[Payload], usb_root: Path, dry_run: bool) -> None:
    for payload in payloads:
        destination = usb_root / payload.relative_destination
        if dry_run:
            print(f"[dry-run] Copy {payload.source} -> {destination}")
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload.source, destination)
        print(f"Copied {payload.source} -> {destination}")


def _remove_stale_payloads(
    payloads: List[Payload], usb_root: Path, dry_run: bool
) -> List[Path]:
    """Remove payloads that exist on the USB drive but not in the manifest."""

    desired = {payload.relative_destination.name for payload in payloads}
    payload_dir = usb_root / "payloads"
    removed: List[Path] = []

    if not payload_dir.exists():
        return removed

    for existing in payload_dir.iterdir():
        if existing.name not in desired and existing.is_file():
            removed.append(existing)
            if dry_run:
                print(f"[dry-run] Remove stale payload {existing}")
            else:
                existing.unlink()
                print(f"Removed stale payload {existing}")
    return removed


def _load_json(path: Path) -> Dict:
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        print(
            f"Warning: existing JSON config at {path} was invalid. "
            f"Backed up to {backup} and starting from a clean configuration.",
            file=sys.stderr,
        )
        return {}


def _save_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _update_control_value(config: Dict, key: str, value: Optional[str]) -> None:
    control = config.setdefault("control", [])

    # Remove existing entry with the same key if present.
    filtered = [entry for entry in control if key not in entry]
    if value is None:
        config["control"] = filtered
        return

    filtered.append({key: value})
    config["control"] = filtered


def _select_default_payload(payloads: List[Payload], selector: Optional[str]) -> Optional[Payload]:
    if not selector:
        return None

    selector = selector.strip()
    if selector.isdigit():
        index = int(selector)
        if index < 0 or index >= len(payloads):
            raise ValueError(f"Default index {index} is out of range for {len(payloads)} payload(s)")
        return payloads[index]

    for payload in payloads:
        if payload.display_name.lower() == selector.lower():
            return payload

    for payload in payloads:
        if selector.lower() in payload.display_name.lower():
            return payload

    raise ValueError(f"Could not match default payload selector '{selector}'")


def _write_manifest(
    payloads: List[Payload],
    usb_root: Path,
    default_payload: Optional[Payload],
    autorun: bool,
    timeout: int,
    dry_run: bool,
) -> None:
    manifest = {
        "payloads": [
            {
                "file": payload.relative_destination.as_posix(),
                "size_bytes": payload.size_bytes,
            }
            for payload in payloads
        ],
        "default_payload": default_payload.relative_destination.as_posix()
        if default_payload
        else None,
        "autorun": autorun,
        "timeout_seconds": timeout,
    }

    manifest_path = usb_root / "ventoy" / "usb-boot-app-manifest.json"
    if dry_run:
        print(f"[dry-run] Would write manifest to {manifest_path}")
        return

    _save_json(manifest_path, manifest)
    print(f"Wrote manifest to {manifest_path}")


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = _parse_args(argv)

    payload_dir = args.payload_dir.expanduser().resolve()
    usb_root = args.usb_root.expanduser().resolve()

    if not usb_root.exists():
        raise FileNotFoundError(f"USB root path not found: {usb_root}")

    if payload_dir == usb_root or usb_root in payload_dir.parents:
        raise ValueError(
            "The payload directory cannot live inside the USB root. "
            "Use a separate staging location so that synchronisation is unambiguous."
        )

    _ensure_ventoy_structure(usb_root)

    discovered_files = _discover_payloads(payload_dir)
    if not discovered_files:
        print(
            f"No payloads with supported extensions {sorted(SUPPORTED_EXTENSIONS)} "
            f"were found in {payload_dir}",
            file=sys.stderr,
        )
        return 1

    payloads = _build_payload_objects(discovered_files)
    _copy_payloads(payloads, usb_root, dry_run=args.dry_run)

    if not args.keep_missing:
        _remove_stale_payloads(payloads, usb_root, dry_run=args.dry_run)

    default_payload: Optional[Payload]
    try:
        default_payload = _select_default_payload(payloads, args.default)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.autorun and default_payload is None:
        default_payload = payloads[0]
        print(
            f"Autorun requested without explicit default; "
            f"using '{default_payload.display_name}'."
        )

    timeout = max(args.timeout, 0)

    ventoy_config_path = usb_root / "ventoy" / "ventoy.json"
    config = _load_json(ventoy_config_path)

    search_root = "/payloads"
    _update_control_value(config, "VTOY_DEFAULT_SEARCH_ROOT", search_root)

    if default_payload:
        default_image = f"/{default_payload.relative_destination.as_posix()}"
        _update_control_value(config, "VTOY_DEFAULT_IMAGE", default_image)
    else:
        _update_control_value(config, "VTOY_DEFAULT_IMAGE", None)

    if args.autorun and default_payload:
        timeout_value = str(min(timeout, 60))
        _update_control_value(config, "VTOY_MENU_TIMEOUT", timeout_value)
        _update_control_value(config, "VTOY_MENU_SHOW_COUNTDOWN", "1")
    else:
        if timeout > 0:
            _update_control_value(config, "VTOY_MENU_TIMEOUT", str(timeout))
        else:
            _update_control_value(config, "VTOY_MENU_TIMEOUT", None)
        _update_control_value(config, "VTOY_MENU_SHOW_COUNTDOWN", None)

    if not args.dry_run:
        _save_json(ventoy_config_path, config)
        print(f"Updated Ventoy configuration at {ventoy_config_path}")
    else:
        print(f"[dry-run] Would update Ventoy configuration at {ventoy_config_path}")

    _write_manifest(payloads, usb_root, default_payload, args.autorun, timeout, args.dry_run)

    print("\nPayload summary:")
    for idx, payload in enumerate(payloads):
        default_marker = " (default)" if default_payload and payload == default_payload else ""
        size_mb = payload.size_bytes / (1024 * 1024)
        print(f"  [{idx}] {payload.display_name} - {size_mb:.2f} MiB{default_marker}")

    if args.autorun and default_payload:
        print(
            f"The drive will automatically boot '{default_payload.display_name}' after "
            f"{timeout} second(s)."
        )
    elif default_payload:
        print(f"The default highlighted menu entry is '{default_payload.display_name}'.")
    else:
        print("No default payload selected; Ventoy will remain on the menu screen.")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())
