# Face profile

Ally's face setup can combine its existing eye tracking, face sensing, and action unit calibration
into one portable profile. The profile contains numeric model coefficients and baseline statistics,
plus setup time, steps, screen dimensions, camera index, and Ally version when supplied by setup.
It never stores a face image, audio, raw landmarks, or per-frame landmark/gaze samples. Profiles
are JSON and must be smaller than 64 KB.

## Where the data lives

The profile is saved locally at `state/face_profile.json`. Applying it writes compatible calibration
values to Ally's existing local files: `state/gaze_calibration.json`, `state/senses_baseline.json`,
and `state/au_baseline.json`. The eye calibration is applied only when its saved screen dimensions
match the current display. The existing senses manager reads the same baseline file.

Cloud sync is **off by default**. To opt in, set these values in the existing `[profile]` section of
`config.toml`:

```toml
[profile]
sync = "spacetimedb"
host = "https://maincloud.spacetimedb.com"
database = "ally-call"
```

When enabled, Ally stores the serialized numeric profile in the private SpacetimeDB `face_profile`
table on `maincloud.spacetimedb.com`, database `ally-call`. A sender-filtered view lets the installation
read only its own row. Ally keeps the bearer identity token in `state/stdb_identity.json`; this secret
is not part of the profile or logs. Sync uses short timeouts, so local save and load continue to work
when the service is unavailable. A profile found only in the cloud is cached to `state/face_profile.json`.

## Show and delete

`python run.py profile show` prints the saved date, which calibration sections are present, and sync
state. It never prints the identity token or calibration coefficients.

`python run.py profile delete` removes the local profile and calibration files and, when sync is on,
deletes the cloud row. If cloud deletion cannot be confirmed, Ally keeps the identity token so the
cloud deletion can be retried. To erase the cloud copy, restore connectivity and run the delete command
again; then turn sync off if you no longer want uploads. To remove the local files manually, delete
`state/face_profile.json`, `state/gaze_calibration.json`, `state/senses_baseline.json`,
`state/au_baseline.json`, `state/stdb_identity.json`, and `state/face_profile_sync.json`.
