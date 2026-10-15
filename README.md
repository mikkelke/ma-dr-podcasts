# DR Podcasts for Music Assistant

A Music Assistant music provider for the DR podcasts that [drpodcast.nu](https://drpodcast.nu/) republishes as RSS.

- Search matches the title and description of every podcast in the drpodcast.nu index, which is cached for 12 hours.
- Browse lists the recently updated podcasts and all podcasts.
- Podcasts added to the Music Assistant library are refreshed by the library sync. Episodes and streams come from the RSS feeds.

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
