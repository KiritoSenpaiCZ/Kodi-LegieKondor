# -*- coding: utf-8 -*-
"""
Legie Kondor (anime4.legiekondor.cz) Subtitles - Kodi subtitle service
addon.

Site notes:
  - No account/login of any kind - every page and every subtitle file is
    public.
  - Small, hand-curated catalog (no pagination) listed at /p/vypis/
    ("Nase preklady"). Anime titles are NOT plain text there (baked into
    cover images) - each card is a JS-driven
    <article onclick="window.location.href='/a/<slug>/'">, so the slug
    list comes from that onclick attribute, and the real title comes
    from each anime page's own <title> tag.
  - An anime's episode list is only a set of cached thumbnail images at
    .../epcache/<slug>/<code>.webp, where <code> = season*100 + episode
    (e.g. "104" = S01E04, "601" = S06E01).
  - The subtitle file for an episode downloads directly and publicly
    from /subdwl/<slug>.<code>/ - normally a plain UTF-8 .ass file
    (starting with "[Script Info]" after a BOM). No cookies, tokens or
    referer needed.
  - The whole-series pack at /packdwl/<slug>/ is deliberately not used.
  - The catalog (slug -> real title) is cached to disk for 24h, since it
    takes one request per anime page to build and rarely changes. The
    per-anime episode list (one request) is always fetched fresh.

Debugging: enable Kodi's debug log (Settings -> System -> Logging),
reproduce, then look for "[LegieKondor]" lines in kodi.log.
"""

import difflib
import html as html_mod
import json
import os
import re
import shutil
import sys
import time
import traceback
import zipfile
from urllib.parse import parse_qs, unquote

import requests
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')

PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
TEMP_DIR = xbmcvfs.translatePath(os.path.join(PROFILE, 'temp', ''))
if not xbmcvfs.exists(TEMP_DIR):
    xbmcvfs.mkdirs(TEMP_DIR)
ROWS_FILE = os.path.join(TEMP_DIR, 'legiekondor_rows.json')
OWNERS_FILE = os.path.join(TEMP_DIR, 'legiekondor_owners.json')
CATALOG_FILE = os.path.join(TEMP_DIR, 'legiekondor_catalog.json')
CATALOG_TTL = 24 * 60 * 60  # 24h - small hand-curated catalog, changes rarely

HANDLE = int(sys.argv[1])

BASE_URL = "https://anime4.legiekondor.cz"
CATALOG_URL = BASE_URL + "/p/vypis/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

LANG_NAME = "Czech"
LANG_FLAG = "cs"

# Anything left behind in TEMP_DIR older than this gets swept on the next
# run - a search+download round trip finishes in well under a minute, so
# anything still there an hour later is leftover, not in-use.
TEMP_MAX_AGE_SECONDS = 3600
# temp-folder entries this addon creates start with TEMP_FILE_PREFIX;
# KEEP_FILES are never swept
TEMP_FILE_PREFIX = 'legiekondor_'
KEEP_FILES = {os.path.basename(ROWS_FILE), os.path.basename(CATALOG_FILE), os.path.basename(OWNERS_FILE)}

# Refuse an implausibly large download or an implausibly large *extracted*
# zip - subtitle files/packs are small, so either cap being hit means
# something's wrong (a huge unexpected response, or a zip bomb).
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024


# ---------------- small helpers ----------------

def log(msg):
    xbmc.log("[LegieKondor] {0}".format(msg), level=xbmc.LOGDEBUG)


def notify(msg):
    xbmcgui.Dialog().notification(ADDON_NAME, msg, xbmcgui.NOTIFICATION_INFO, 4000)


def get_params():
    raw = sys.argv[2] if len(sys.argv) > 2 else ""
    return parse_qs(raw.lstrip('?'))


def save_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception as e:
        log("failed to save {0}: {1}".format(path, e))


def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


# >>> shared block "kodi_temp" - edit dev/shared/kodi_temp.py in KiritoSenpaiCZ.github.io, then run dev/sync.py
def current_video():
    """Path of the video Kodi has loaded - playing OR paused - else None.
    (Player.Playing alone isn't enough: it's false while paused.)"""
    try:
        if xbmc.getCondVisibility('Player.HasVideo') or xbmc.getCondVisibility('Player.Paused'):
            return xbmc.getInfoLabel('Player.Filenameandpath') or None
    except Exception:
        pass
    return None


def _read_owners():
    try:
        with open(OWNERS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_owners(owners):
    try:
        with open(OWNERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(owners, f)
    except Exception as e:
        log("couldn't save {0}: {1}".format(OWNERS_FILE, e))


def remember_owner(path):
    """Record which video a delivered subtitle belongs to, so
    cleanup_temp_dir() never deletes it while that video is still loaded
    (e.g. paused for more than an hour)."""
    video = current_video()
    if not video:
        return
    try:
        top = os.path.relpath(path, TEMP_DIR).split(os.sep)[0]
    except ValueError:
        return
    if not top or top.startswith('..'):
        return
    owners = _read_owners()
    owners[top] = video
    _write_owners(owners)


def cleanup_temp_dir():
    """Sweep old downloads out of TEMP_DIR. Kept: KEEP_FILES (small caches
    managed by their own logic), and every subtitle belonging to the video
    Kodi currently has loaded - playing or paused - however old it is, so a
    long pause can't delete a subtitle that's still in use."""
    keep = KEEP_FILES
    owners = _read_owners()
    video = current_video()
    try:
        now = time.time()
        for name in os.listdir(TEMP_DIR):
            if name in keep or not name.startswith(TEMP_FILE_PREFIX):
                continue
            if video and owners.get(name) == video:
                continue
            path = os.path.join(TEMP_DIR, name)
            try:
                age = now - os.path.getmtime(path)
            except OSError:
                continue
            if age < TEMP_MAX_AGE_SECONDS:
                continue
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
            except OSError as e:
                log("cleanup: couldn't remove {0}: {1}".format(path, e))
        existing = set(os.listdir(TEMP_DIR))
        still_there = dict((k, v) for k, v in owners.items() if k in existing)
        if still_there != owners:
            _write_owners(still_there)
    except Exception as e:
        log("cleanup_temp_dir failed: {0}".format(e))
# <<< shared block "kodi_temp"


# ---------------- title/query cleanup ----------------

def clean_release_title(name):
    if not name:
        return name
    name = re.sub(r'\.\w+$', '', name)
    name = re.sub(r'\[[^\]]*\]', ' ', name)
    name = re.sub(r'\([^)]*\)', ' ', name)
    name = re.sub(r'[._]', ' ', name)

    lower = name.lower()
    cut_at = None
    m = re.search(r's\d{1,2}e\d{1,2}', lower)
    if m:
        cut_at = m.start()
    keywords = [
        "2160p", "1080p", "720p", "480p", "4k",
        "blu-ray", "bluray", "bdrip", "webrip", "web-dl", "web dl",
        "hdtv", "dvdrip", "hdrip",
        "x264", "x265", "h264", "h265", "hevc", "avc",
        "dual audio", "dual-audio", "multi audio", "multi-audio",
        "aac", "flac", "dts", "opus",
    ]
    for kw in keywords:
        idx = lower.find(kw)
        if idx != -1 and (cut_at is None or idx < cut_at):
            cut_at = idx
    if cut_at is not None:
        name = name[:cut_at]

    name = re.sub(r'\s-\s*\d+.*$', '', name)
    name = re.sub(r'[-–—]+\s*$', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def extract_season_episode(text):
    if not text:
        return text, None, None
    m = re.search(r'[sS](\d{1,2})[eE](\d{1,3})', text)
    if not m:
        return text, None, None
    season = int(m.group(1))
    episode = int(m.group(2))
    cleaned = (text[:m.start()] + text[m.end():]).strip()
    cleaned = clean_release_title(cleaned) or cleaned
    return cleaned, season, episode


def pick_best_match(query, shows):
    if not shows:
        return None
    titles_lower = [s['title'].lower() for s in shows]
    close = difflib.get_close_matches(query.lower(), titles_lower, n=1, cutoff=0.0)
    if close:
        return shows[titles_lower.index(close[0])]
    return shows[0]


def get_allowed_languages(params):
    raw = params.get('languages', [''])[0]
    if not raw:
        return None
    return set(unquote(n) for n in raw.split(','))


# ---------------- site access ----------------

def fetch_catalog_slugs(session):
    """Returns [slug, ...] from the /a/<slug>/ onclick links on the
    catalog listing page."""
    try:
        resp = session.get(CATALOG_URL, headers=HEADERS, timeout=15)
        html_text = resp.text
    except Exception as e:
        log("catalog page fetch failed: {0}".format(e))
        return []
    slugs = re.findall(r"window\.location\.href='/a/([a-z0-9\-]+)/'", html_text)
    # de-dupe, keep order
    seen = set()
    out = []
    for s in slugs:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def fetch_anime_title(session, slug):
    try:
        resp = session.get("{0}/a/{1}/".format(BASE_URL, slug), headers=HEADERS, timeout=15)
        html_text = resp.text
    except Exception as e:
        log("anime page fetch failed for {0}: {1}".format(slug, e))
        return None
    m = re.search(r'<title>\s*(.*?)\s*(?:\|\|.*)?</title>', html_text, re.DOTALL)
    if not m:
        return None
    return html_mod.unescape(m.group(1)).strip()


def build_catalog(session):
    slugs = fetch_catalog_slugs(session)
    log("catalog: {0} slug(s) found on {1}".format(len(slugs), CATALOG_URL))
    shows = []
    for slug in slugs:
        title = fetch_anime_title(session, slug)
        if title:
            shows.append({"slug": slug, "title": title})
        else:
            log("could not resolve a title for slug '{0}', skipping".format(slug))
    save_json(CATALOG_FILE, {"ts": time.time(), "shows": shows})
    return shows


def get_catalog(session):
    cached = load_json(CATALOG_FILE)
    if cached and (time.time() - cached.get("ts", 0)) < CATALOG_TTL and cached.get("shows"):
        log("using cached catalog ({0} show(s), age {1:.0f}s)".format(
            len(cached["shows"]), time.time() - cached["ts"]))
        return cached["shows"]
    log("catalog cache missing/stale - rebuilding")
    return build_catalog(session)


def fetch_episode_codes(session, slug):
    """Returns [(season, episode, code), ...] parsed from the anime page's
    epcache thumbnail images - no login, no extra request needed beyond
    this one page."""
    try:
        resp = session.get("{0}/a/{1}/".format(BASE_URL, slug), headers=HEADERS, timeout=15)
        html_text = resp.text
    except Exception as e:
        log("episode list fetch failed for {0}: {1}".format(slug, e))
        return []
    codes = re.findall(r'/epcache/{0}/(\d{{3,4}})\.webp'.format(re.escape(slug)), html_text)
    out = []
    seen = set()
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        code = int(c)
        season = code // 100
        episode = code % 100
        out.append((season, episode, c))
    out.sort()
    return out


def guess_extension(content_disposition, body_sample):
    m = re.search(r'filename\*?=[^\'"]*[\'"]?([^\'";\r\n]+)', content_disposition or "")
    if m:
        ext_m = re.search(r'\.([A-Za-z]+)$', m.group(1))
        if ext_m:
            return "." + ext_m.group(1)
    try:
        sample_text = body_sample.decode('utf-8', 'ignore')
    except Exception:
        sample_text = ""
    if sample_text.strip().startswith("[Script Info]"):
        return ".ass"
    return ".srt"


# ---------------- Kodi-facing actions ----------------

def append_subtitle(lang_name, flag_code, label2, url_params):
    listitem = xbmcgui.ListItem(label=lang_name, label2=label2)
    if flag_code:
        listitem.setArt({"thumb": flag_code})
    listitem.setProperty("sync", "false")
    listitem.setProperty("hearing_imp", "false")
    url = "plugin://{0}/?{1}".format(
        ADDON_ID,
        "&".join("{0}={1}".format(k, requests.utils.quote(str(v))) for k, v in url_params.items())
    )
    xbmcplugin.addDirectoryItem(handle=HANDLE, url=url, listitem=listitem, isFolder=False)


def handle_search(params, is_manual):
    season = episode = None
    if is_manual:
        query_raw = params.get('searchstring', [''])[0]
        query, season, episode = extract_season_episode(query_raw)
        if not query:
            query = clean_release_title(query_raw)
    else:
        tvshow = xbmc.getInfoLabel("VideoPlayer.TVshowtitle")
        title = tvshow or xbmc.getInfoLabel("VideoPlayer.OriginalTitle") or xbmc.getInfoLabel("VideoPlayer.Title")
        season_label = xbmc.getInfoLabel("VideoPlayer.Season")
        episode_label = xbmc.getInfoLabel("VideoPlayer.Episode")
        if season_label.isdigit():
            season = int(season_label)
        if episode_label.isdigit():
            episode = int(episode_label)

        if title:
            query = clean_release_title(title)
        else:
            try:
                filename = os.path.basename(unquote(xbmc.Player().getPlayingFile()))
            except Exception:
                filename = ""
            query, s2, e2 = extract_season_episode(filename)
            query = clean_release_title(query)
            if season is None:
                season = s2
            if episode is None:
                episode = e2

    query = (query or "").strip()
    if not query:
        log("no usable search query, aborting")
        return

    log("query='{0}' season={1} episode={2} manual={3}".format(query, season, episode, is_manual))

    session = requests.Session()
    shows = get_catalog(session)
    log("{0} show(s) in catalog".format(len(shows)))
    if not shows:
        return

    best = pick_best_match(query, shows)
    log("picked show: '{0}' (slug={1})".format(best['title'], best['slug']))

    codes = fetch_episode_codes(session, best['slug'])
    log("{0} episode(s) found for '{1}'".format(len(codes), best['title']))
    if not codes:
        return

    if season is not None and episode is not None:
        matching = [c for c in codes if c[0] == season and c[1] == episode]
        if matching:
            codes = matching
        else:
            log("no episode matched S{0:02d}E{1:02d}, showing full list instead".format(season, episode))
    elif season is not None:
        matching = [c for c in codes if c[0] == season]
        if matching:
            codes = matching
        else:
            log("no episodes matched season {0}, showing full list instead".format(season))

    allowed_langs = get_allowed_languages(params)
    if allowed_langs and LANG_NAME not in allowed_langs:
        log("Czech filtered out by allowed_langs, nothing to show")
        return

    saved = {}
    shown = 0
    for i, (s, e, code) in enumerate(codes):
        rid = str(i)
        saved[rid] = {"slug": best['slug'], "code": code, "season": s, "episode": e}
        label2 = "S{0:02d}E{1:02d}".format(s, e)
        append_subtitle(LANG_NAME, LANG_FLAG, label2, {"action": "download", "rid": rid})
        shown += 1

    save_json(ROWS_FILE, saved)
    log("listed {0} subtitle(s) for '{1}'".format(shown, best['title']))


def handle_download(params):
    rid = params.get('rid', [None])[0]
    if rid is None:
        notify("Nothing to download.")
        return
    rows = load_json(ROWS_FILE) or {}
    row = rows.get(rid)
    if not row:
        notify("Subtitle info expired - please search again.")
        return

    download_url = "{0}/subdwl/{1}.{2}/".format(BASE_URL, row['slug'], row['code'])
    try:
        resp = requests.get(download_url, headers=HEADERS, timeout=20)
        content = resp.content
        content_disposition = resp.headers.get('Content-Disposition', '')
    except Exception as e:
        log("download failed: {0}".format(e))
        notify("Download failed (see debug log).")
        return

    if not content or content.lstrip()[:1] == b'<':
        notify("Download failed - unexpected response (see debug log). Try again in a moment.")
        log("download got empty/HTML body for slug={0} code={1} (status {2})".format(
            row['slug'], row['code'], resp.status_code))
        return

    if len(content) > MAX_DOWNLOAD_BYTES:
        notify("Download refused - file is larger than expected (see debug log).")
        log("download refused: {0} bytes exceeds MAX_DOWNLOAD_BYTES {1}".format(
            len(content), MAX_DOWNLOAD_BYTES))
        return

    safe_name = "{0}_{1}".format(row['slug'], row['code'])
    is_zip = content[:2] == b'PK'

    if is_zip:
        zip_path = os.path.join(TEMP_DIR, "legiekondor_{0}_{1}.zip".format(safe_name, int(time.time())))
        try:
            with open(zip_path, 'wb') as f:
                f.write(content)
        except Exception as e:
            log("failed to write zip file: {0}".format(e))
            notify("Downloaded but couldn't save the file (see debug log).")
            return

        extract_dir = os.path.join(TEMP_DIR, "legiekondor_{0}_{1}".format(safe_name, int(time.time())))
        try:
            with zipfile.ZipFile(zip_path) as zf:
                extracted_size = sum(info.file_size for info in zf.infolist())
                if extracted_size > MAX_EXTRACTED_BYTES:
                    notify("Download refused - archive is larger than expected when extracted (see debug log).")
                    log("refusing to extract {0}: extracted size {1} exceeds MAX_EXTRACTED_BYTES {2}".format(
                        zip_path, extracted_size, MAX_EXTRACTED_BYTES))
                    return
                zf.extractall(extract_dir)
        except Exception as e:
            log("zip extract failed: {0}".format(e))
            notify("Downloaded a zip but couldn't extract it (see debug log).")
            return

        sub_file = None
        for root, _dirs, files in os.walk(extract_dir):
            for fn in files:
                if fn.lower().endswith(('.srt', '.ass', '.sub')):
                    sub_file = os.path.join(root, fn)
                    break
            if sub_file:
                break
        if not sub_file:
            notify("Downloaded and extracted, but no .srt/.ass file found inside.")
            log("no subtitle file found after extracting {0}".format(zip_path))
            return
        filepath = sub_file
    else:
        ext = guess_extension(content_disposition, content[:200])
        filename = "legiekondor_{0}_{1}{2}".format(safe_name, int(time.time()), ext)
        filepath = os.path.join(TEMP_DIR, filename)
        try:
            with open(filepath, 'wb') as f:
                f.write(content)
        except Exception as e:
            log("failed to write subtitle file: {0}".format(e))
            notify("Downloaded but couldn't save the file (see debug log).")
            return

    remember_owner(filepath)

    log("saved subtitle to {0}".format(filepath))
    listitem = xbmcgui.ListItem(label=os.path.basename(filepath))
    xbmcplugin.addDirectoryItem(handle=HANDLE, url=filepath, listitem=listitem, isFolder=False)


def run():
    try:
        cleanup_temp_dir()
        params = get_params()
        action = params.get('action', [''])[0]
        log("action={0} params={1}".format(action, params))
        if action == 'search':
            handle_search(params, is_manual=False)
        elif action == 'manualsearch':
            handle_search(params, is_manual=True)
        elif action == 'download':
            handle_download(params)
        else:
            log("unknown/missing action, nothing to do")
    except Exception as e:
        log("unhandled exception: {0}\n{1}".format(e, traceback.format_exc()))
    finally:
        xbmcplugin.endOfDirectory(HANDLE)


if __name__ == '__main__':
    run()
