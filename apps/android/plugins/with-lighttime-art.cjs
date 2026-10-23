const { withDangerousMod } = require('expo/config-plugins');
const { mkdir, readFile, writeFile } = require('node:fs/promises');
const path = require('node:path');

module.exports = function withLighttimeArt(config) {
  return withDangerousMod(config, ['android', async (mod) => {
    const resources = path.join(mod.modRequest.platformProjectRoot, 'app/src/main/res');
    const mark = await readFile(path.join(mod.modRequest.projectRoot, 'assets/lighttime-mark.xml'), 'utf8');
    const save = async (relative, contents) => {
      const target = path.join(resources, relative);
      await mkdir(path.dirname(target), { recursive: true });
      await writeFile(target, contents);
    };
    const legacy = mark.replace(/<path /, '<path android:fillColor="#101214" android:pathData="M0,0 H108 V108 H0 Z" /><path ');
    const adaptive = `<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
  <background android:drawable="@color/lighttime_launcher_background" />
  <foreground android:drawable="@drawable/lighttime_mark" />
  <monochrome android:drawable="@drawable/lighttime_mark" />
</adaptive-icon>`;
    await Promise.all([
      save('values/lighttime.xml', '<resources><color name="lighttime_launcher_background">#101214</color></resources>'),
      save('drawable-anydpi/lighttime_mark.xml', mark),
      save('mipmap-anydpi/ic_launcher.xml', legacy),
      save('mipmap-anydpi/ic_launcher_round.xml', legacy),
      save('mipmap-anydpi-v26/ic_launcher.xml', adaptive),
      save('mipmap-anydpi-v26/ic_launcher_round.xml', adaptive),
      save('drawable-anydpi/splashscreen_logo.xml', `<?xml version="1.0" encoding="utf-8"?>
<layer-list xmlns:android="http://schemas.android.com/apk/res/android">
  <item android:drawable="@color/lighttime_launcher_background" />
  <item android:gravity="center" android:drawable="@drawable/lighttime_mark" />
</layer-list>`),
    ]);
    return mod;
  }]);
};
