# MURCLOUD releases

| File | Package | Version | SHA-256 |
|---|---|---|---|
| `MURCLOUD-10.0.0-beta.apk` | `com.artyonix.murcloud` | 10.0.0-beta (100000) | `5f981bb25835e58ddac8d67ef03917d54e5753c51ba81d33c1a9748bc539eaac` |

Release build (R8-minified), signed with APK Signature Scheme v2.
Signing certificate: `CN=MURCLOUD, O=ARTYONIX STUDIOS, C=RU`,
SHA-256 `7b59a82d56c79bc8f3cd652a5992a3225a7d2eefaa50dc1e0c3f5c5158dbbcc8`.

Install: copy to the phone and open it (allow "Install unknown apps" for your file manager),
or `adb install MURCLOUD-10.0.0-beta.apk`. On first start enter your server address (https://…).

The signing key is not in this repository. Future updates must be signed with the same key,
otherwise Android refuses to install them over this version.
