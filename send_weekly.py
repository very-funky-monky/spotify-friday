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
since = (now - dt.timedelta(days=DAYS_BACK)).date()

# Sorrend számít: az első találó kategória nyer
CATEGORIES = [
    ("Rap / Hip-hop", ["rap", "hip hop", "trap"]),
    ("Elektronikus", ["electro", "house", "techno", "edm", "dance", "trance", "drum and bass"]),
    ("Rock / Alternatív", ["rock", "metal", "punk", "indie", "alternative"]),
    ("Népzene / World", ["folk", "nepzene", "népzene", "world", "tanchaz"]),
    ("Pop", ["pop"]),
]
HU_MARKERS = ["hungarian", "magyar"]


def spotify_token():
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def get(url, headers, **kw):
    for _ in range(5):
        r = requests.get(url, headers=headers, timeout=30, **kw)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "2")) + 1)
            continue
        return r
    return r


def main():
    h = {"Authorization": f"Bearer {spotify_token()}"}
    api = "https://api.spotify.com/v1"

    # 1) Friss albumok/kislemezek keresése
    albums = {}
    for offset in range(0, 200, 10):
        r = requests.get(f"{api}/search", headers=h, timeout=30, params={
            "q": "tag:new", "type": "album", "market": MARKET, "limit": 10, "offset": offset})
        r.raise_for_status()
        items = r.json()["albums"]["items"]
        if not items:
            break
        for a in items:
            if a["release_date"] >= str(since):
                albums[a["id"]] = a

    # 2) Előadók műfajai (egyesével, mert a többes végpont 403-at adott)
    artist_ids = sorted({ar["id"] for a in albums.values() for ar in a["artists"]})
    genres, no_genre_field, failed = {}, 0, 0
    for aid in artist_ids:
        r = get(f"{api}/artists/{aid}", h)
        if r.status_code != 200:
            failed += 1
            if failed <= 3:
                print(f"Előadó lekérés hiba: {r.status_code} {r.text[:200]}")
            continue
        data = r.json()
        if "genres" not in data:
            no_genre_field += 1
        genres[aid] = data.get("genres", [])
    print(f"{len(albums)} friss album, {len(artist_ids)} előadó, "
          f"{failed} sikertelen lekérés, {no_genre_field} előadónál nincs 'genres' mező.")
    if artist_ids and (failed == len(artist_ids) or no_genre_field == len(artist_ids)):
        sys.exit("A Spotify nem ad műfajadatot ehhez az apphoz, másik szűrési megoldás kell.")

    # 3) Csak magyar előadók, majd számok lekérése
    hu_albums = []
    for a in albums.values():
        g = [x for ar in a["artists"] for x in genres.get(ar["id"], [])]
        if any(m in x for x in g for m in HU_MARKERS):
            hu_albums.append((a, g))

    by_cat = defaultdict(list)
    for a, g in hu_albums:
        r = get(f"{api}/albums/{a['id']}/tracks", h, params={"market": MARKET, "limit": 50})
        if r.status_code != 200:
            print(f"Album számlista hiba: {r.status_code} {a['name']}")
            continue
        gl = " ".join(g).lower()
        cat = next((n for n, keys in CATEGORIES if any(k in gl for k in keys)), "Egyéb")
        artists = ", ".join(x["name"] for x in a["artists"])
        for t in r.json()["items"]:
            by_cat[cat].append((artists, t["name"], t["external_urls"]["spotify"]))

    # 4) HTML e-mail
    order = [n for n, _ in CATEGORIES] + ["Egyéb"]
    parts = [f"<h2>Magyar megjelenések – {now:%Y. %m. %d.}</h2>"]
    total = 0
    for cat in order:
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
