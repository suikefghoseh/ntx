# ntx

Hourly collector of public trend feeds for the Hype Radar dashboard.

`collect.py` reads Google Trends (US and UK), Wikipedia's most viewed articles, Apple's top songs and top free iPhone games, the Know Your Meme newsfeed and a few Google News searches. The `collect` workflow runs it every hour and force-pushes the result as `feeds.json` to the `data` branch, which always holds a single commit.

A Claude routine downloads `feeds.json` once an hour, uses it next to its own web searches to judge which trends young people are into, and writes the list to the dashboard. Nothing else writes to this repository.

Standard library only. To try it: `python3 collect.py feeds.json`.
