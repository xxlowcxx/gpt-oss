# Felony's Big Booter – the all-in-one upload, update, repair app

Felony's Big Booter (powered by Felony Logix and delivered by T00L-A1D) is a
Python helper that stages an "all-in-one" USB drive capable of presenting a
GRUB menu of ISO/WIM images, firmware utilities and maintenance payloads.  The
focus remains on automating the file layout and configuration so that you can
drop operating system installers or recovery environments into the USB stick
without rebuilding the bootloader for every change.

## Capability overview

| Scenario | What the helper provides |
| --- | --- |
| Booting operating-system media | Detects `.iso`, `.img` and `.wim` files under `images/` and renders GRUB menu entries. Loopback booting works for most Linux distributions that expose `/casper` or `/live` kernels, and WIM files can be launched through `wimboot`. Windows installers copied from official media typically succeed, but Apple no longer distributes bootable macOS ISOs and they require their own USB creation workflow. iOS devices cannot be booted from GRUB at all. |
| Firmware updates | Any vendor-supplied `.efi` utility that you copy into `efi-tools/` appears under the **Firmware utilities** submenu. The helper does **not** download, validate or automate BIOS updates; it simply launches the tools you stage. |
| Operating system repair | Place your own recovery ISOs (Linux live environments, WinPE images, etc.) inside `maintenance/` to expose them under the maintenance submenu. The provided `run-maintenance.sh` is only a placeholder; real repairs require you to supply the tooling. |
| System update scans | The helper itself cannot scan installed operating systems for updates. Instead, boot into an OS installer or maintenance environment that provides the update/repair workflows you need. |

The project intentionally avoids promising universal compatibility.  It gives
you a curated structure, prebuilt GRUB configuration and optional automation so
that you can build a trustworthy toolkit with the software you select.

### Platform compatibility notes

- **Linux** – Works best with distributions that ship loopback-friendly
  installers (Ubuntu, Debian, Fedora live media, etc.).  Others may require
  unpacking kernels and initrds manually.
- **Windows** – Supported through `wimboot` when you provide official ISO/WIM
  media.  Secure Boot must be disabled unless you sign the binaries yourself.
- **macOS** – Apple provides the `createinstallmedia` tool to build bootable USB
  installers from a Mac.  Dropping macOS app bundles or DMGs into `images/`
  will **not** produce a bootable entry.
- **iOS/iPadOS** – Handheld devices do not boot from external USB mass storage
  and are outside the scope of GRUB entirely.
- **Firmware/BMC tooling** – Only vendor-supplied `.efi` binaries that you
  stage manually can be launched.  Automatic scanning/updating is not possible.

The solution is **not** a full replacement for specialised projects such as
[Ventoy](https://www.ventoy.net) or [SARDU](https://www.sarducd.it).  Those
solutions rely on custom boot sectors and complex kernel patches.  The helper
here stays intentionally simple so that it can be audited, reproduced and
modified for enterprise environments.

## Requirements

1. A USB stick of at least 16 GB, preferably 32 GB+
2. GRUB 2 tooling installed on your workstation (`grub-mkstandalone`,
   `grub-install`, `grub-mkrescue`)
3. `wimboot` (from https://ipxe.org/wimboot) if you want to boot Windows
   PE/WIM images
4. Administrative privileges to install the bootloader onto the USB stick
5. Optional automation dependencies when using `--prepare-device`:
   - `sgdisk` (part of `gdisk`)
   - `mkfs.fat`/`mkfs.vfat`
   - `mkfs.exfat` (or `mkfs.ntfs`, `mkfs.ext4` when selecting alternative filesystems)
   - `mount`, `umount`, `lsblk`, `partprobe` (usually provided by util-linux)

When you use the automated provisioning flow (`--prepare-device` together with
`--embed-grub`), the helper handles partitioning, formatting and staging the
bootloader directly onto the USB stick.  There is no need to rely on imaging
utilities such as Rufus, balenaEtcher or Ventoy – the Python script prepares the
device in place using standard Linux tooling.

The helper is written in pure Python.  By default it only touches files inside
the staging folder and therefore can be executed safely without elevated
privileges.  When `--prepare-device` is used it will repartition and format the
target USB drive automatically, so run that mode with care.

## Quick start

1. (Optional) Let the helper create the full USB layout for you without any
   third-party flashing tools.  This wipes the selected device, creates the
   required partitions and mounts the data volume automatically:

   ```bash
   sudo python examples/multiboot_usb/multiboot_usb.py \
     --prepare-device \
     --device /dev/sdX \
     --mount-point /media/multiboot \
     --embed-grub
   ```

   Replace `/dev/sdX` with the USB device node (double-check with `lsblk`).
   The helper creates a 512 MiB FAT32 EFI System Partition plus an exFAT data
   partition labelled `MULTIBOOT`.  Change `--data-filesystem` to `ntfs` or
   `ext4` if you prefer a different format.  After the command finishes the
   data partition is mounted at `/media/multiboot` and staged automatically.

2. If you prefer manual preparation, format the USB drive using GPT with the
   following partitions:
   - FAT32 ESP (~512 MiB) flagged as bootable
   - exFAT or NTFS partition for large ISO files
   Mount the main data partition somewhere convenient, for example
   `/media/usb`, before running the remaining steps.

3. Run the helper to scaffold the folder structure.  Add `--embed-grub` to
   automatically build a standalone GRUB EFI binary (requires the
   `grub-mkstandalone` tool):

   ```bash
   python examples/multiboot_usb/multiboot_usb.py /media/usb \
     --install-maintenance \
     --embed-grub \
     --stdout
   ```

   The `--stdout` flag prints the generated GRUB configuration so that you can
   review it before writing.

4. If `--embed-grub` succeeded you will find a ready-to-boot EFI loader at
   `EFI/BOOT/BOOTX64.EFI` inside the staging directory.  Copy the entire
   directory structure to your USB drive.  This covers most UEFI-only systems.

   The helper prints `[!]` warnings if prerequisites such as `grub-mkstandalone`
   are missing.  For legacy BIOS support or if you skipped `--embed-grub`,
   install GRUB onto the USB manually (this still requires root privileges):

   ```bash
   sudo grub-install \
     --target=x86_64-efi \
     --removable \
     --efi-directory=/media/usb/boot \
     --boot-directory=/media/usb/boot
   ```

   Add `--target=i386-pc --boot-directory=/media/usb/boot /dev/sdX` if you want
   legacy BIOS support as well.  Replace `/dev/sdX` with the raw device node of
   your USB drive.

5. Copy operating system installers into `/media/usb/images` (or the mount
   point you selected in step 1).  The helper will
   detect `.iso`, `.img` and `.wim` files automatically.  Place any UEFI
   firmware update utilities (usually `.efi` files from the vendor) inside the
   `/media/usb/efi-tools` directory.

6. Re-run the helper whenever you add, remove or rename files.  Include
   `--embed-grub` again so the embedded loader stays in sync:

   ```bash
   python examples/multiboot_usb/multiboot_usb.py /media/usb --embed-grub
   ```

7. Safely eject the USB drive and boot from it.  GRUB will display a menu that
   contains all detected installers, firmware tools and the optional
   maintenance shell.

## Automated USB provisioning

The `--prepare-device` flag is designed to make the setup fully hands-off:

- It **destroys any existing data** on the target device.  Double-check the
  value passed via `--device` with `lsblk` or `sudo fdisk -l` before running.
- A 512 MiB FAT32 EFI System Partition and a large data partition (default
  exFAT) are created.  Adjust `--esp-size-mib` or `--data-filesystem` to suit
  your workflow.
- After formatting, the script mounts the data partition at `--mount-point`
  and continues staging the GRUB configuration, catalog metadata and optional
  maintenance helper.
- When `--embed-grub` is supplied the USB is immediately UEFI-bootable without
  additional tooling.

You can re-run the helper at any time with the same mount point.  It updates
the GRUB menu dynamically based on the images you drop into the USB drive.

## Maintenance toolkit

The script can install a placeholder helper at
`maintenance/run-maintenance.sh`.  Replace it with a proper environment:

- **Linux** – Copy a minimal Linux kernel and initrd (for example
  [SystemRescue](https://www.system-rescue.org/)) into the `maintenance`
  directory.  The helper adds loopback entries so the menu appears
  automatically.
- **Windows** – Use the [ADK](https://learn.microsoft.com/windows-hardware/get-started/adk-install)
  to build a WinPE WIM image, then copy the resulting `boot.wim` into the
  `maintenance` folder.  The GRUB configuration automatically boots WIM files
  using `wimboot`.

Inside the maintenance environment you can script BIOS flashes, run disk
repairs, reset passwords and more.  Because the menu is generated from the
filesystem there is no need to rebuild the USB when you update your toolset.

## FAQ

**Can it boot macOS installers?**

macOS installers can be restored to a DMG/ISO image.  Place the resulting ISO
inside the `images` folder.  Apple hardware may enforce signature checks, so
use official installers.

**Can it repair existing installations automatically?**

The helper provides the scaffolding.  Drop your preferred recovery ISO (e.g.
SystemRescue, Hiren's BootCD, WinRE) into the `maintenance` folder and they
will appear in the GRUB menu.

**Can it update firmware automatically?**

Firmware updates are vendor specific.  Many vendors provide UEFI `.efi`
utilities that can be chainloaded directly from GRUB.  Copy them into the
`efi-tools` directory.  For vendor utilities that require Windows, include a
WinPE image with the OEM tool pre-installed.

**Does this make iOS bootable?**

iOS devices use a secure boot process that cannot be replicated from generic
PC firmware.  You can, however, include iTunes or Apple Configurator inside a
WinPE environment to restore devices.

## Safety considerations

- Always verify checksums of downloaded installers and firmware.
- Keep your BIOS/UEFI configuration backed up before applying updates.
- Test the USB in a virtual machine (e.g. QEMU, VirtualBox) before relying on
  it in production.
- Maintain clear documentation of where each ISO/WIM file came from for
  compliance and auditing.
