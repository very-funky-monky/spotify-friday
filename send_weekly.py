import os, re, sys, time, html, datetime as dt
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

# Jellegzetesen magyar szavak (angolban/más nyelvben ritka). Ezekre keresünk, és
# ezek alapján ismerjük fel a magyar címet. Nyugodtan bővítheted.
HU_WORDS = {
    "szeretlek", "szerelem", "szerelmem", "szív", "szívem", "szívverés", "nélkül", "nélküled",
    "éjszaka", "éjjel", "álom", "álmok", "csillag", "csillagok", "könnyek", "könny", "fény",
    "sötét", "sötétben", "vissza", "mindig", "soha", "együtt", "veled", "nekem", "neked",
    "magyar", "budapest", "haza", "hazám", "élet", "életem", "halál", "kék", "piros", "fekete",
    "fehér", "tűz", "víz", "föld", "szél", "angyal", "bolond", "gyere", "menj", "maradj",
    "holnap", "tegnap", "hajnal", "este", "reggel", "város", "kislány", "anyám", "apám",
    "barátom", "szabad", "boldog", "szomorú", "bocsánat", "köszönöm", "tavasz", "nyár", "ősz",
    "tél", "eső", "hazug", "igaz", "csak", "kell", "akarom", "tudom", "vagyok", "nincs",
    "minden", "semmi", "valaki", "senki", "újra", "végre", "miért", "hogyan", "mikor",
}
STRONG_CHARS = "őűŐŰ"


def is_hungarian(text):
    if any(c in text for c in STRONG_CHARS):
        return True
    return any(w in HU_WORDS for w in re.findall(r"\w+", text.lower()))


def get(url, headers=None, **kw):
    r = None
    for attempt in range(5):
        try:
            r = requests.get(url, headers=headers, timeout=30, **kw)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 0)) if r.status_code == 429 else 0
            time.sleep(max(wait, 2 * (attempt + 1)))
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
    """artists.txt: soronként egy előadó neve (biztosan magyarnak számít)."""
    out = set()
    if os.path.exists("artists.txt"):
        for line in open("artists.txt", encoding="utf-8"):
            line = line.split("|")[0].strip()
            if line and not line.startswith("#"):
                out.add(line.lower())
    return out


def main():
    h = {"Authorization": f"Bearer {spotify_token()}"}
    allow = load_allowlist()
    albums, errors = {}, 0

    def collect(q, pages=2):
        nonlocal errors
        for offset in range(0, pages * 10, 10):
            r = get(f"{SP}/search", h, params={
                "q": q, "type": "album", "market": MARKET, "limit": 10, "offset": offset})
            if r is None or r.status_code != 200:
                errors += 1
                if errors <= 3:
                    print(f"Keresés hiba {getattr(r, 'status_code', 'nincs válasz')}: {q}")
                continue
            items = r.json()["albums"]["items"]
            if not items:
                return
            for a in items:
                if a["release_date"] >= since and a.get("album_type") != "compilation":
                    albums[a["id"]] = a

    # 1) Felfedezés: általános minta + magyar szavakra célzott keresések + saját lista
    collect("tag:new", 20)
    for w in sorted(HU_WORDS):
        collect(f"{w} tag:new")
    for name in sorted(allow):
        collect(f'artist:"{name}" tag:new', 1)

    # 2) Magyar felismerés cím / előadónév / saját lista alapján
    hu = []
    for a in albums.values():
        artists = ", ".join(x["name"] for x in a["artists"])
        if (is_hungarian(a["name"]) or is_hungarian(artists)
                or any(x["name"].lower() in allow for x in a["artists"])):
            hu.append((artists, a))
    print(f"{len(albums)} friss kiadás, ebből {len(hu)} magyarnak felismerve, "
          f"{errors} sikertelen keresés.")
    for artists, a in hu[:15]:
        print(f"  {artists} – {a['name']}")

    # 3) Számok lekérése
    entries = []
    for artists, a in sorted(hu, key=lambda x: x[0].lower()):
        r = get(f"{SP}/albums/{a['id']}/tracks", h, params={"market": MARKET, "limit": 50})
        if r is None or r.status_code != 200:
            continue
        tracks = [(t["name"], t["external_urls"]["spotify"]) for t in r.json()["items"]]
        entries.append((artists, a, tracks))

    # 4) HTML e-mail
    parts = [f"<h2>Magyar megjelenések – {now:%Y. %m. %d.}</h2>"]
    total = sum(len(t) for _, _, t in entries)
    for artists, a, tracks in entries:
        url = a["external_urls"]["spotify"]
        parts.append(f'<p><b>{html.escape(artists)}</b> – '
                     f'<a href="{url}">{html.escape(a["name"])}</a> '
                     f'<small>({a["album_type"]}, {a["release_date"]})</small></p>')
        if len(tracks) > 1:
            parts.append("<ul>")
            for title, turl in tracks:
                parts.append(f'<li><a href="{turl}">{html.escape(title)}</a></li>')
            parts.append("</ul>")
    if not entries:
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
    print(f"Elküldve, {len(entries)} kiadás, {total} szám.")


if __name__ == "__main__":
    main()
