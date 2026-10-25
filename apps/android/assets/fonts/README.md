# Bundled type

Poppins supplies the interface text and values. Playfair Display supplies the
short display headings. The app loads these local files with `expo-font`; no
font download or network connection is needed at runtime.

The original font files are unmodified. `PlayfairDisplay.ttf` is the variable
weight font. `provenance.json` records the pinned Google Fonts commit, original
URLs and SHA-256 digests. Family copyright and full SIL Open Font License 1.1
texts are retained beside the files. The Android art config plugin also copies
both license texts into the APK's `assets/licenses/` directory.

Use these registered family names consistently in native and layout-fixture
styles: `Poppins-Regular`, `Poppins-Medium`, `Poppins-SemiBold` and
`PlayfairDisplay`.
