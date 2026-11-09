# Personal providers

Optional SoundCloud discovery/account implementation; excluded from public packages.

- `discovery.py`: artwork candidates and artist suggestions.
- `connection.py`: connection factory, Keychain credentials and disposable account metadata.
- `operations.py`: bounded provider operations and cancellation.
- Core `soundbox.providers` admits the fixed module/actions from the signed build profile.

Core owns curation, artwork publication, workers and device authority.
Startup readiness never means starting a search or accessing live credentials in tests.
Never log tokens or store them in library state, backups or build output.

Check both package profiles and isolated provider tests through `./check.sh`.
See [provider boundaries](../docs/release-boundaries.md) and [connection behavior](../docs/soundcloud-connection.md).
