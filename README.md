# Legie Kondor Subtitles - Kodi Subtitle Addon

Kodi subtitle service addon for anime4.legiekondor.cz — Czech anime subtitles, no account required.

Compatible with Kodi 19, 20, and 21.

## Current Version
service.subtitles.legiekondor - 1.1.1

## Installation Instructions
Recommended: install through the [Highflight Subtitles Repository](https://github.com/KiritoSenpaiCZ/KiritoSenpaiCZ.github.io), which also handles updates.

Manual install:
1. Download `service.subtitles.legiekondor-1.1.1.zip` from this repo (or build it from source)
2. In Kodi: **Add-ons > Install from zip file**, select the zip

## Setup Instructions
None — the whole site is public, no login needed.

## How it works
- Matches Kodi's video metadata (or a manual search) against Legie Kondor's anime catalog
- Downloads the matching episode's subtitle directly
- Whole-season bulk downloads aren't supported (not needed for single-episode playback)
- Downloaded files are validated before use (rejects HTML error pages and oversized responses/archives) and old temp files are swept automatically

## Issues
Please open an issue in this repo with a description of the problem and, if possible, a Kodi debug log (Settings > System > Logging > Enable debug logging, then grep `kodi.log` for `[LegieKondor]`).
