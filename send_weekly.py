import os, sys, time, html, datetime as dt
from collections import defaultdict
from zoneinfo import ZoneInfo
import requests

# --- Időzítés: a két cron közül csak az fusson, ami budapesti idő szerint 6 óra
now = dt.datetime.now(ZoneInfo("Europe/Budapest"))
if os.getenv("GITHUB_EVENT_NAME") == "schedule" and now.hour != 6:
    print("Nem 6 óra van Budapesten, kihagyom.")
    sys.exit(0)

MARKET = "HU"
DAYS_BACK = 7
since = str((now - dt.timedelta(days=DAYS_BACK)).date())
SP = "https://api.spotify.com/v1"
MB = "https://musicbrainz.org/ws/2"
MB_HEADERS = {"User-Agent": f"hu-friday-releases/1.0 ({os.getenv('MAIL_TO', 'unknown')})"}

# Sorrend számít: az első találó kategória nyer
CATEGORIES = [
    ("Rap / Hip-hop", ["rap", "hip hop", "hip-hop", "trap"]),
    ("Elektronikus", ["electro", "house", "techno", "edm", "dance", "trance", "drum and bass"]),
    ("Rock / Alternatív", ["rock", "metal", "punk", "indie", "alternative"]),
    ("Népzene / World", ["folk", "world", "népzene", "nepzene"]),
    ("Pop", ["pop"]),
]
CAT_NAMES = [n for n, _ in CATEGORIES] + ["Egyéb"]


def get(url, headers=None, **kw):
    r = None
    for _ in range(5):
        r = requests.get(url, headers=headers, timeout=30, **kw)
        if r.status_code in (429, 503):
            time.sleep(int(r.headers.get("Retry-After", "2")) + 1)
            continue
        return r
    return r


def spotify_token():
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def load_allowlist():
    """artists.txt: soronként 'Név' vagy 'Név | Kategória'."""
    out = {}
    if os.path.exists("artists.txt"):
        for line in open("artists.txt", encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, cat = line.partition("|")
            out[name.strip().lower()] = cat.strip() or None
    return out


def category_from(tags):
    text = " ".join(tags).lower()
    return next((n for n, keys in CATEGORIES if any(k in text for k in keys)), "Egyéb")


_mb_cache = {}


def musicbrainz(name):
    """(magyar-e, kategória) a MusicBrainz alapján; csak pontos névegyezés számít."""
    key = name.lower()
    if key in _mb_cache:
        return _mb_cache[key]
    time.sleep(1.1)  # MusicBrainz: max ~1 kérés/mp
    result = (False, None)
    try:
        r = get(f"{MB}/artist", MB_HEADERS, params={
            "query": f'artist:"{name}"', "fmt": "json", "limit": 5})
        if r.status_code == 200:
            for a in r.json().get("artists", []):
                if a.get("name", "").lower() == key and a.get("country") == "HU":
                    tags = [t["name"] for t in sorted(
                        a.get("tags", []), key=lambda t: -t.get("count", 0))]
                    result = (True, category_from(tags))
                    break
        else:
            print(f"MusicBrainz hiba {r.status_code}: {name}")
    except requests.RequestException as e:
        print(f"MusicBrainz hálózati hiba: {name}: {e}")
    _mb_cache[key] = result
    return result


def main():
    h = {"Authorization": f"Bearer {spotify_token()}"}
    allow = load_allowlist()

    # 1) Friss kiadások keresése
    albums = {}

    def collect(q, pages):
        for offset in range(0, pages * 10, 10):
            r = get(f"{SP}/search", h, params={
                "q": q, "type": "album", "market": MARKET, "limit": 10, "offset": offset})
            if r.status_code != 200:
                print(f"Keresés hiba {r.status_code}: {q}")
                return
            items = r.json()["albums"]["items"]
            if not items:
                return
            for a in items:
                if a["release_date"] >= since and a.get("album_type") != "compilation":
                    albums[a["id"]] = a

    collect("tag:new", 20)
    for name in allow:
        collect(f'artist:"{name}" tag:new', 1)

    # 2) Magyar előadók azonosítása
    names = {ar["name"] for a in albums.values() for ar in a["artists"]}
    info, lookups = {}, 0
    for n in sorted(names):
        if n.lower() in allow:
            info[n] = (True, allow[n.lower()] or musicbrainz(n)[1] or "Egyéb")
        else:
            lookups += 1
            info[n] = musicbrainz(n)
    hu_count = sum(1 for v in info.values() if v[0])
    print(f"{len(albums)} friss kiadás, {len(names)} előadó, "
          f"{lookups} MusicBrainz keresés, {hu_count} magyar előadó.")

    # 3) Számok lekérése a magyar kiadásokhoz
    by_cat = defaultdict(list)
    for a in albums.values():
        hu = [info[ar["name"]] for ar in a["artists"] if info[ar["name"]][0]]
        if not hu:
            continue
        cat = hu[0][1] or "Egyéb"
        r = get(f"{SP}/albums/{a['id']}/tracks", h, params={"market": MARKET, "limit": 50})
        if r.status_code != 200:
            print(f"Számlista hiba {r.status_code}: {a['name']}")
            continue
        artists = ", ".join(x["name"] for x in a["artists"])
        for t in r.json()["items"]:
            by_cat[cat].append((artists, t["name"], t["external_urls"]["spotify"]))

    # 4) HTML e-mail
    parts = [f"<h2>Magyar megjelenések – {now:%Y. %m. %d.}</h2>"]
    total = 0
    for cat in CAT_NAMES:
        tracks = sorted(by_cat.get(cat, []))
        if not tracks:
            continue
        total += len(tracks)
        parts.append(f"<h3>{html.escape(cat)} ({len(tracks)})</h3><ul>")
        for artist, title, url in tracks:
            parts.append(f'<li>{html.escape(artist)} – <a href="{url}">{html.escape(title)}</a></li>')
        parts.append("</ul>")
    if total == 0:
        parts.append("<p>Ezen a héten nem találtam új magyar megjelenést.</p>")

    # 5) Küldés Brevóval
    r = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json"},
        json={
            "sender": {"email": os.environ["MAIL_FROM"], "name": "Heti magyar zenék"},
            "to": [{"email": os.environ["MAIL_TO"]}],
            "subject": f"Magyar megjelenések – {now:%Y.%m.%d.}",
            "htmlContent": "".join(parts),
        },
        timeout=30,
    )
    r.raise_for_status()
    print(f"Elküldve, {total} szám.")


if __name__ == "__main__":
    main()
