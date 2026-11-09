# Build profiles

Signed package composition inputs, validated by `soundbox.providers` and `scripts/build_composition.py`.

- `public.json`: shared local app; excludes the current personal provider/dependencies.
- `personal.json`: explicitly admits the known SoundCloud module and actions.

Frozen apps trust only their bundled manifest; settings/environment cannot enable excluded code.
Source runs may select `SOUNDBOX_PROFILE=public`.

Build from the root with `./build_app.sh` or `./build_app.sh public`; only personal is installed here.
See [composition and distribution boundaries](../docs/release-boundaries.md).
