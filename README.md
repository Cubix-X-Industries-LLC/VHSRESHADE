# VHSRESHADE

Custom Modrinth ReShade installer for Minecraft Java profiles.

## What it does

- Downloads the latest official ReShade installer from [reshade.me](https://reshade.me/index.php)
- Lets the user choose a Modrinth profile
- Clones the right Modrinth Java runtime into that profile as `reshade-java`
- Installs ReShade into the cloned `javaw.exe` in OpenGL mode
- Copies the bundled Kyubus preset that is currently being used
- Updates the selected Modrinth profile's `override_java_path` automatically

## Repository contents

- `modrinth_reshade_installer.pyw`: full source for the installer
- `kyubus-current-preset/`: the active preset config bundled with the installer
- `TRANSPARENCY_AND_PRIVACY.txt`: plain-language behavior and privacy notes
- `SHA256.txt`: hashes for the built distribution artifacts and source bundle files
- `INSTALLER_NOTES.txt`: short installer overview

## Current bundled preset

The installer currently bundles:

- `kyubus-current-preset/ReShade.ini`
- `kyubus-current-preset/kyubus-FrutigerAero-CRT.ini`

## Trust notes

- ReShade itself is downloaded from the official site at install time.
- The installer source is included so people can inspect exactly what it does.
- The embedded preset is also included in source form for inspection.
- No antivirus or malware-scanner bypass behavior was added.

## Usage

1. Run the installer.
2. Pick the Modrinth profile you want.
3. Let it download and install ReShade.
4. Restart Modrinth if it was already open.
