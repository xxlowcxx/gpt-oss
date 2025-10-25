# USB Boot App – Ventoy payload orchestrator

This guide explains how to turn the accompanying `usb_boot_app.py` helper into
an "all-in-one" USB stick that can automatically boot installer payloads (ISO,
WIM, VHD, etc.) directly from BIOS or UEFI firmware.

The approach builds on the [Ventoy](https://www.ventoy.net/en/index.html)
project, which provides a reliable multi-boot USB loader.  Ventoy only needs to
be installed on the USB drive once; afterwards you simply copy new payloads onto
the stick and update a small JSON configuration file.  The helper script
automates those file management tasks so that you can:

1. Stage new installers in a local folder.
2. Run the helper to copy them onto the USB stick.
3. (Optionally) configure the drive to autorun a default payload after a menu
   countdown.

## Requirements

* Python 3.9 or later.
* A USB drive that has already been initialised with Ventoy (see below). The
  helper now validates that the USB root contains Ventoy's `ventoy/` folder
  before making any changes, so run the official Ventoy installer first.
* Operating-system level permissions to copy files onto the USB drive.

## 1. Prepare the USB drive with Ventoy (one-time step)

Download Ventoy for your platform and install it on the USB drive:

1. Fetch the latest Ventoy release from the [official download page](https://www.ventoy.net/en/download.html).
2. Extract the archive.
3. Run the platform-specific installer (e.g. `Ventoy2Disk.exe` on Windows or
   `sudo ./Ventoy2Disk.sh -i /dev/sdX` on Linux/macOS) to install Ventoy onto the
   USB device. This wipes the drive, so make sure you have backups of any
   existing data.

After installation you will see a `ventoy` directory and a standard data
partition on the USB stick.  The stick is now a blank multi-boot drive.

## 2. Stage your payloads

Create a local staging directory and populate it with the installers you want to
boot.  The helper supports the following file types out of the box:

```
.iso  .img  .wim  .vhd  .vhdx  .efi  .zip  .dmg  .gz  .xz
```

> **Tip:** For Windows installers you can place `.iso` or `.wim` files.  For
> Linux distributions use their `.iso` files.  macOS `.dmg` files can be added
> so long as you have a compatible boot loader (Ventoy can chainload them when
> run on Apple silicon/Intel hardware).

## 3. Sync the payloads onto the USB drive

From the repository root run:

```bash
python examples/usb_boot_app.py \
  --payload-dir /path/to/staging \
  --usb-root /media/youruser/VentoyUSB \
  --autorun --default 0 --timeout 3
```

**Arguments:**

| Flag | Description |
| --- | --- |
| `--payload-dir` | Local folder containing the installers to copy. |
| `--usb-root` | Mounted path to the Ventoy USB drive. |
| `--autorun` | Automatically boot the default payload after the timeout expires. |
| `--default` | Which payload to boot by default. Use an index (`0`, `1`, …), a full file name, or a substring. |
| `--timeout` | Seconds Ventoy waits before launching the default payload. |
| `--dry-run` | Preview the operations without touching the USB drive. |
| `--keep-missing` | Leave payloads on the drive even if they are not present locally. |

The helper copies every supported file into `<USB-ROOT>/payloads/` and updates
`<USB-ROOT>/ventoy/ventoy.json` with the appropriate Ventoy plugin controls.
If the staging directory accidentally lives inside the USB drive (which could
otherwise create recursive copies), the helper aborts with a clear error.

* `VTOY_DEFAULT_SEARCH_ROOT` limits Ventoy's scanning to the `payloads/`
  directory.
* `VTOY_DEFAULT_IMAGE` points to the chosen default payload.
* `VTOY_MENU_TIMEOUT` and `VTOY_MENU_SHOW_COUNTDOWN` enforce an autorun style
  boot experience when `--autorun` is supplied.

A manifest is also written to
`<USB-ROOT>/ventoy/usb-boot-app-manifest.json`, listing all payloads and the
current default entry so that you can audit the drive later.

## 4. Boot from BIOS/UEFI

With the USB stick prepared you can boot any supported machine:

1. Plug the USB drive into the host.
2. Enter the firmware (BIOS/UEFI) boot selector (e.g. `F12`, `F2`, `Esc`, or
   `Option` on Macs) and choose the Ventoy USB device.
3. Ventoy will display its menu. If autorun is configured, the default payload
   launches automatically after the countdown. Otherwise you can select any
   payload manually.

## Frequently asked questions

### Can I update the USB stick without erasing it?

Yes. Ventoy separates the boot loader from your payload partition.  You can add
or remove payload files at any time using the helper script.  If a new version
of Ventoy is released you can upgrade it in-place with the official installer
(`Ventoy2Disk` offers an `-u` upgrade flag on Linux/macOS and an upgrade button
in the Windows GUI).

### Does this work for macOS installers?

Ventoy can boot macOS recovery installers on compatible Apple hardware.  Copy
the `.dmg` installer into your staging folder and run the helper.  When the
drive boots you will see it listed in the Ventoy menu.  macOS may require an
internet connection to download additional components during installation.

### How do I add Windows Autounattend profiles?

Create a folder named `ventoy/auto_install/` on the USB drive and drop your JSON
templates there.  Afterwards update `ventoy.json` manually or extend the helper
script to include the `auto_install` block referencing your ISO and template.

### I need persistence for Linux distributions

Ventoy supports persistence plugins.  Create persistent images with the
`CreatePersistentImg.sh` script provided by Ventoy, copy them to the USB drive,
and reference them in `ventoy.json`.  The helper writes a manifest that you can
extend with persistence metadata if required.

### Can this unlock BIOS/firmware options or flash devices automatically?

No.  The helper focuses exclusively on file management for a Ventoy USB stick
and intentionally avoids touching firmware or low-level configuration.  Any
attempt to reflash BIOS/UEFI firmware or unlock hidden settings should be
performed with the official tooling provided by the hardware vendor because of
the significant risk of bricking the device.

In fact, the helper now refuses to sync files that look like firmware flashing
artefacts (for example `bios-update.bin` or `thinkpad_firmware.rom`).  When it
sees a combination of known firmware extensions and BIOS-related names it stops
with a clear error so that the potentially dangerous payload never reaches the
USB drive.

### Why is BIOS unlocking or firmware flashing out of scope?

Ventoy's design (and therefore this helper) stops at handing payloads to the
machine's standard boot flow.  Unlocking hidden firmware features or pushing
unofficial BIOS images can permanently damage hardware, void warranties, and in
many jurisdictions violates software licensing or security laws.  Those
operations also vary dramatically between vendors, chipsets, and board
revisions, making it impossible to provide a safe, generic automation path.

If you must update firmware, rely on the manufacturer-supplied utilities and
follow their documentation carefully.  Keep your Ventoy USB for staging
installer payloads only—this separation keeps the workflow portable while
avoiding the legal and safety risks associated with firmware modification.

---

With these steps you have a reusable "app" for managing your BIOS-bootable USB
collection of installers.  Drop new files into the staging folder, rerun the
helper, and the Ventoy drive stays ready to go.
