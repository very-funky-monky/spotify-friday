name: Heti magyar megjelenések

on:
  schedule:
    - cron: "15 4 * * 5"   # 6:15 nyári időszámítás (CEST)
    - cron: "15 5 * * 5"   # 6:15 téli időszámítás (CET)
  workflow_dispatch:        # kézi teszteléshez

jobs:
  send:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python send_weekly.py
        env:
          SPOTIFY_CLIENT_ID: ${{ secrets.SPOTIFY_CLIENT_ID }}
          SPOTIFY_CLIENT_SECRET: ${{ secrets.SPOTIFY_CLIENT_SECRET }}
          BREVO_API_KEY: ${{ secrets.BREVO_API_KEY }}
          MAIL_TO: ${{ secrets.MAIL_TO }}
          MAIL_FROM: ${{ secrets.MAIL_FROM }}
