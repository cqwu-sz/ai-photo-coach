# iOS Info.plist required keys (P0-3.5)

Cursor cannot edit Xcode-generated Info.plists directly. Open the
target in Xcode → "Info" tab → add the keys below. The strings are
the production-ready Chinese descriptions reviewers expect for an
App Store submission.

```xml
<key>NSCameraUsageDescription</key>
<string>用于环境扫描视频与构图建议拍摄。我们不会上传原始视频，仅提取关键帧进行分析。</string>

<key>NSPhotoLibraryAddUsageDescription</key>
<string>把你修好的照片保存到相册。</string>

<key>NSPhotoLibraryUsageDescription</key>
<string>读取你最近拍摄照片的 EXIF（焦段、ISO 等）来对推荐方案做闭环校准。</string>

<key>NSLocationWhenInUseUsageDescription</key>
<string>用 GPS 定位匹配附近经过验证的机位 / 室内地标，并计算太阳方位与黄金时刻。位置只在分析期间使用，不会长期保留。</string>

<key>NSMotionUsageDescription</key>
<string>记录环视与漫游过程中的设备姿态，用于推断真实机位坐标。</string>

<key>NSContactsUsageDescription</key>
<!-- Not currently used; remove if Apple flags. -->
<string>暂未使用。</string>

<key>ITSAppUsesNonExemptEncryption</key>
<false/>
```

## App Tracking Transparency

We don't use IDFA / cross-app tracking, so no `NSUserTrackingUsageDescription` needed. If we ever wire StoreKit ads / Branch links, add:

```xml
<key>NSUserTrackingUsageDescription</key>
<string>用于评估推荐转化效果，不会与广告主共享个人信息。</string>
```

## App Attest

Add the entitlement (target → Signing & Capabilities → +Capability → "App Attest"). For production:

```xml
<key>com.apple.developer.devicecheck.appattest-environment</key>
<string>production</string>
```

Use `development` while in Xcode debug builds.

## Sign in with Apple (A0-12 / Apple 4.5.4)

Once you offer ANY login or account system, SIWA must be available too.

1. Xcode target → Signing & Capabilities → +Capability → "Sign in with Apple"
2. App Store Connect → App Information → Sign in with Apple → enable
3. Make sure `AccountView` is reachable from your Settings entry point.

## Privacy Manifest (A0-13 / iOS 17+)

`ios/AIPhotoCoach/App/PrivacyInfo.xcprivacy` is committed and must be
added to the app target's "Copy Bundle Resources" build phase (Xcode
adds it automatically on drag-and-drop into the project navigator).

To regenerate the privacy report run **Product → Archive →
Distribute → Generate App Privacy Report**; it consumes our
`PrivacyInfo.xcprivacy` plus any third-party `.xcprivacy` shipped by
SDKs in `Frameworks/`.

## In-App Purchase (A0-7 / Apple 3.1.1)

1. App Store Connect → In-App Purchases → +Auto-Renewable Subscription
   - Reference name: `AI Photo Coach Pro - Monthly`
   - Product ID: `ai_photo_coach.pro.monthly`
   - Subscription group: `Pro`
   - Price: ¥18 / month (adjust as desired)
2. Once approved, set `IAPManager.useShadowPro = false` (already the
   default in current code).
3. Configure Server-to-Server notifications V2:
   - URL: `https://<prod>/apple/asn`
   - Sandbox URL: `https://<staging>/apple/asn`
4. (Optional but recommended) Generate App Store Server API key and put
   the .p8 path / issuer / key id in env (`APPLE_IAP_*`) — this lets
   the backend pull historical receipts in case a webhook is missed.
