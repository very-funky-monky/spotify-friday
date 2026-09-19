import os, sys, html, datetime as dt
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


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


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

    # 2) Előadók műfajai
    artist_ids = sorted({ar["id"] for a in albums.values() for ar in a["artists"]})
    genres = {}
    for c in chunks(artist_ids, 50):
        r = requests.get(f"{api}/artists", headers=h, params={"ids": ",".join(c)}, timeout=30)
        r.raise_for_status()
        for ar in r.json()["artists"]:
            if ar:
                genres[ar["id"]] = ar.get("genres", [])

    # 3) Csak magyar előadók, majd számok lekérése
    hu_albums = []
    for a in albums.values():
        g = [x for ar in a["artists"] for x in genres.get(ar["id"], [])]
        if any(m in x for x in g for m in HU_MARKERS):
            hu_albums.append((a, g))

    by_cat = defaultdict(list)
    for c in chunks([a["id"] for a, _ in hu_albums], 20):
        r = requests.get(f"{api}/albums", headers=h, params={"ids": ",".join(c), "market": MARKET}, timeout=30)
        r.raise_for_status()
        full = {a["id"]: a for a in r.json()["albums"] if a}
        for a, g in hu_albums:
            if a["id"] not in full:
                continue
            gl = " ".join(g).lower()
            cat = next((n for n, keys in CATEGORIES if any(k in gl for k in keys)), "Egyéb")
            artists = ", ".join(x["name"] for x in a["artists"])
            for t in full[a["id"]]["tracks"]["items"]:
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
