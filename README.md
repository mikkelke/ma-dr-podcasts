# DR Podcasts for Music Assistant

A Music Assistant music provider for the DR podcasts that [drpodcast.nu](https://drpodcast.nu/) republishes as RSS, and for the DR live radio channels.

- Search matches the title and description of every podcast in the drpodcast.nu index, which is cached for 12 hours, and the name of every radio channel.
- Browse lists the recently updated podcasts, all podcasts and the live radio channels.
- Podcasts added to the Music Assistant library are refreshed by the library sync. Episodes and streams come from the RSS feeds.
- The radio channels are P1, P2, P3, P4 København, P5 København, P6 Beat and P8 Jazz. The library sync keeps all of them in the library.
- A channel's HLS stream url is read from its dr.dk/lyd page when it is played and kept for 12 hours. If the page cannot be read, the last url that was found is used, and before any was found the fixed url in `dr_podcasts/radio.py`.

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
