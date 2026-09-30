# Kodi-LegieKondor

A Kodi subtitle addon (`service.subtitles.legiekondor`) for [Legie Kondor](https://anime4.legiekondor.cz/) — Czech anime subtitles.

## What it does

- Matches Kodi's video metadata (show title, season, episode) - or a manual search - against Legie Kondor's small, hand-curated anime catalog
- Downloads the matching episode's subtitle directly - **no account, login or password of any kind needed**, the whole site is public
- Whole-season bulk download ("packdwl") is not supported - not needed for personal use

## Installation

1. Download the repo as a zip, or build `service.subtitles.legiekondor-1.0.0.zip` from this repo's contents
2. In Kodi: **Add-ons → Install from zip file**, select the zip

## How it works

- The catalog (anime slug → real title) is read from the site's `/p/vypis/` listing page and cached locally for 24h, since building it takes one request per anime page
- Each anime's episode list is read from the small thumbnail images on its own page - their filenames embed a `season*100 + episode` code (e.g. `104` = S01E04), which doubles as the subtitle's direct download path (`/subdwl/<slug>.<code>/`)
- No session/login caching needed at all - there's no login

## Related

- [Kodi-Hiyori](https://github.com/KiritoSenpaiCZ/Kodi-Hiyori) — hiyori.cz
- [Kodi-Wosir](https://github.com/KiritoSenpaiCZ/Kodi-Wosir) — wosir.cz
- [Kodi-Edna](https://github.com/KiritoSenpaiCZ/Kodi-Edna) — edna.cz
- [Kodi-Kamui](https://github.com/KiritoSenpaiCZ/Kodi-Kamui) — kamui-subs.cz
- [Kodi-NyaSub](https://github.com/KiritoSenpaiCZ/Kodi-NyaSub) — nyasub.cz
