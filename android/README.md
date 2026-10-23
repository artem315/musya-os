# MURCLOUD for Android

A small, hardened WebView client for a **real** MURCLOUD server. The desktop,
MUR AI, Service Engine, Drive and MUR PC all run on the backend; the app adds:

* connection screen with server-address validation and a `/api/health` check
  (refuses anything that is not a MURCLOUD backend);
* HTTPS only (release builds have no cleartext at all; debug builds may use
  `http://10.0.2.2` / `localhost` for development);
* certificate errors are never bypassed;
* offline screen with retry, automatic reload when the network returns;
* file uploads (`<input type=file>`) through the system picker;
* downloads from MUR Drive via `DownloadManager` (cookie sent to the
  MURCLOUD origin only) into `Downloads/MURCLOUD/`;
* links to other sites open in the system browser, not inside the
  authenticated WebView; no file/content access, no mixed content, Safe Browsing on;
* session data excluded from cloud backups / device transfer;
* adaptive MURCLOUD icon, touch-first (the desktop UI has a mobile layout).

No root needed. `minSdk 26` (Android 8.0), `targetSdk 36`.

## Build

```bash
cd android
export ANDROID_HOME=/path/to/android-sdk     # platforms;android-36, build-tools;36.0.0
./gradlew assembleDebug testDebugUnitTest lintDebug
# → app/build/outputs/apk/debug/app-debug.apk  (installable, debug-signed)
```

Release signing uses environment variables only (`MUR_KEYSTORE_FILE`,
`MUR_KEYSTORE_PASSWORD`, `MUR_KEY_ALIAS`, `MUR_KEY_PASSWORD`); CI reads them
from repository secrets. Without them `assembleRelease` produces an unsigned,
R8-minified APK. Pre-fill the server field with `-PmurServer=https://your.host`.
