# DR Podcasts for Music Assistant

A Music Assistant music provider for the DR podcasts and the DR live radio channels. It reads from one source, DR's own radio API (`https://api.dr.dk/radio/v5`), with the public key that DR's web app sends. Nothing is scraped from web pages.

- Search matches the title and description of every DR podcast and the name of every radio channel. The list of podcasts (about 700) is kept for an hour.
- Browse lists the recently updated podcasts, all podcasts and the live radio channels.
- A podcast and its episodes are identified by DR's production numbers, which do not change when a title does. The episodes come from DR's episode groups, so podcasts that are made of other podcasts list all of their episodes. A podcast and its episode list are kept for 15 minutes.
- An episode plays from its mp3 with the highest bitrate, else from its HLS stream. The link is read from DR every time an episode is played.
- The radio channels are those that DR's web app lists as stations: P1, P2, P3, P6 Beat, P8 Jazz and the districts of P4 and P5, each with the stream and the logo DR lists. The HLS stream is used, else the best ICY one. The list is kept for 12 hours.
- Podcasts added to the Music Assistant library are brought up to date from DR by the library sync, and the ones DR no longer has leave the library. The library sync keeps the national radio channels in the library, and the district channels that were added to it.
- The title of a playing radio channel is the track DR lists as playing (artist and title, the composers for classical music), else the programme on air, else the channel name. It is read from DR's radio API, at most one request every 30 seconds per list. DR lists a track about 50 seconds after it starts, and a channel's track list is served without a key only while DR's CDN holds a copy, so a title can be late and quiet channels show their programme more often.
- When DR cannot be reached, the last copy of the channels, the podcasts and the episodes is used.

## Install

Copy the package to the Music Assistant host and mount it read-only into the container:

```sh
rsync -a --delete --exclude=__pycache__ --chown=root:root --rsync-path="sudo rsync" \
  dr_podcasts/ <box>:/data/ma-dr-podcasts/dr_podcasts/
```

```yaml
    volumes:
      - /data/ma-dr-podcasts/dr_podcasts:/app/venv/lib/python3.14/site-packages/music_assistant/providers/dr_podcasts:ro
```

Recreate the container, then add the "DR Podcasts" provider under Settings > Providers. It needs no configuration.

## Tests

```sh
python3 -m unittest discover -s tests
```
