# VS-0 research record

This is the dated acquisition research record. For the current application method,
see [How it works](how_it_works.md); for physical checks and later recognition
evidence, see [Validation history](validation.md) and [Examples](examples.md).

Inspected 2026-09-08/09. No device measurements are implied by this document.

## Source snapshots and releases

* Official [polarofficial/polar-ble-sdk](https://github.com/polarofficial/polar-ble-sdk/tree/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11): commit `3d15da61dd0c63e6be2582d03fd80f6c7ba02e11`, podspec 8.2.0; latest GitHub release 8.2.0, published 2026-08-18.
* [Root AGENTS.md](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/AGENTS.md) inspected: runtime capability queries, serialize device operations, use current APIs, propagate errors. This is reference material for our separate Python project. No official SDK code is vendored.
* [zHElEARN/polar-python](https://github.com/zHElEARN/polar-python/tree/e5129c0bfa789044b29e8e1f1ba186912b23bf3a): commit `e5129c0bfa789044b29e8e1f1ba186912b23bf3a`, project/PyPI 1.1.1. Read README, `examples/polar_verity_sense.py`, device, settings, frame, ACC, gyro and compression code. No GitHub latest-release record was published (API 404); PyPI is the package source.
* Checked PyPI JSON before pinning: polar-python 1.1.1, Bleak 3.0.2, matplotlib 3.11.1, pytest 9.1.1, Ruff 0.16.6, hatchling 1.32.0. NumPy latest 2.5.3 requires Python 3.12; selected supported stable 2.4.6 (`>=3.11`) for the requested Python 3.11 environment. See `https://pypi.org/pypi/<package>/<version>/json` for release metadata. Exact runtime versions are saved per session.
* CI uses current stable [actions/checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) and [actions/setup-python v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0), checked against release metadata and action manifests on 2026-09-09. Both use Node 24; this avoids the Node 20 deprecation warning observed on the initial passing CI run.

## Verified from official documentation/source

Sources at the official commit above:

* [Verity Sense product guide](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/documentation/products/PolarVeritySense.md)
* [Online PMD specification, v1.0 August 2024](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/technical_documentation/online_measurement.pdf), especially tables 3-8, 11-12 and section 4.3
* [Time system](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/documentation/TimeSystemExplained.md) and [iOS timestamp reconstruction](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/sources/iOS/ios-communications/Sources/iOSCommunications/ble/api/model/gatt/client/pmd/model/PmdTimeStampUtils.swift)
* iOS `AccData.swift`, `GyrData.swift`, `PmdDataFrame.swift` in the adjacent PMD source directories.

Normal mode: ACC 52 Hz, +/-8 g, 16-bit; gyro 52 Hz, +/-2000 degrees/s, 16-bit. ACC output is mg and gyro degrees/s. HR, PPG, PPI and magnetometer are also listed. SDK mode expands live ACC/gyro rates to 26/52/104/208/416 Hz and ranges, but disables HR/PPI. We never send the SDK-mode command. Availability of a stream does not prove the requested combination works on this particular sensor; query and start ACC then gyro, with both subscriptions kept active. HR is a separate standard BLE service; attempt it after IMU startup, without requesting PPI or PPG.

PMD notifications carry measurement type, **one 64-bit little-endian timestamp for the final sample**, frame type, and raw or delta-compressed samples. The original epoch is 2000-01-01, units nanoseconds. The official SDK reconstructs the first batch from nominal sample rate, and subsequent batches by interpolating between consecutive final-sample timestamps. The code uses integer arithmetic in our collector to avoid precision loss at large epochs. Raw header timestamps are never discarded.

The PDF prose mentions an 8-bit ring counter, but its table 6 and current SDK parser use the 8-byte timestamp header and expose no packet sequence number for these streams. Exact over-the-air packet losses are consequently **not measurable** here. Gap and missing-sample estimates are evidence, not packet-loss counts.

Device clocks may be unset or use local-time conventions. The collector does not set device time or present device timestamp as verified UTC. Host UTC and monotonic arrival clocks are separately stored. Known issue: Verity Sense stream time may not adopt `setLocalTime` until a power cycle. See [known issues](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/documentation/KnownIssues.md).

## Python implementation decision

The preferred library was inspected first, but its 1.1.1 high-level API is insufficient for lossless acquisition without adaptation:

* `PmdDataFrame` adds the Unix epoch offset to the original timestamp.
* ACC conversion truncates scaled values to integer mg.
* Settings responses expose an error code but `start_stream` does not reject it; stop responses are not consumed and can contaminate the next request.
* Several decoders silently accept truncated payloads. One-bit signed delta sign extension is omitted.
* High-level callbacks expose parsed samples without original notification bytes.

We reuse public `MeasurementSettings`, enums and UUIDs from pinned polar-python, with a small directly owned Bleak transport and strict IMU decoder. The decoder is independently implemented from the specification; raw packets and scaling factors permit future re-decoding. Only required ACC/gyro formats are accepted. Unsupported formats fail visibly. No SDK implementation is copied wholesale, no private library attributes are patched, and no global monkeypatch is used. Unit tests cover the risks above.

## Offline recording research

Update 2026-09-10: a narrow Windows/Bleak implementation now provides explicit sensor-memory start/stop and download without requiring an iOS application. See [implementation, workflow and acceptance status](offline_recording.md). The notes below record the original SDK investigation; triggers, encrypted files and a native mobile app are outside the implemented scope.

Read [offline guide](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/documentation/SdkOfflineRecordingExplained.md), [offline technical PDF](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/technical_documentation/offline_measurement.pdf), current iOS `PolarOfflineRecordingApi.swift`, `PolarOfflineRecordingTrigger` model, and `examples/example-ios/polar-sensor-data-collector/PSDC/PolarBleSdkManager.swift`. Current official iOS API uses async/await since 8.0; Android uses coroutines since 7.0. The guide contains older Android-style feature names; use the current native API enums when implementing iOS later.

* Verity Sense firmware 2.1.0 onward supports offline HR, ACC, gyro, PPG, PPI and magnetometer per product guide.
* ACC and gyro are independently started data types; trigger settings support a map of multiple types. Simultaneous ACC + gyro offline recording is the intended supported configuration, but actual combination, duration, battery and capacity remain hardware acceptance questions.
* Normal ACC/gyro settings are 52 Hz. The SDK-mode offline options are 13/26/52 Hz, lower than online maxima. Query `requestOfflineRecordingSettings`; full options use `requestFullOfflineRecordingSettings`. SDK mode again excludes HR/PPI.
* Wait for offline feature readiness, query `getAvailableOfflineRecordingDataTypes`, query settings, then call `startOfflineRecording` sequentially for each type. Check `getOfflineRecordingStatus`. Stop each with `stopOfflineRecording`.
* Recordings continue after BLE disconnect. `listOfflineRecordings` supplies entries; `getOfflineRecord` downloads and decodes an entry; `removeOfflineRecord` explicitly removes it. Stop before fetching. Files may not appear for up to five minutes because of buffering.
* `setOfflineRecordingTrigger` supports `TRIGGER_SYSTEM_START`: automatic capture each power-up. Trigger configuration changes take effect next startup. Exercise-start triggers also exist; `TRIGGER_DISABLED` disables future starts and does not by itself stop an active recording. Query with `getOfflineRecordingTriggerSetup`. Preserve per-type settings.
* Triggered recording ends on explicit stop or device power-off. Verify a new file after the next power-up in a later hardware test.
* Online and offline capture of the **same** type conflict (`ERROR_ALREADY_IN_STATE`); standard HR is the documented exception. Do not start live ACC when offline ACC is running.
* Verity Sense must be in sensor/heart mode (blue side LED) for file operations. Internal training/swimming mode returns `SYSTEM_BUSY`.
* No automatic deletion to free memory. Later app must inspect space, download/verify then offer explicit deletion. Optional AES-128 encryption requires retaining the same key for download.
* PPI requests change HR update behavior and may abort an internal training session; we do not request PPI. Skin-contact status is documented as unreliable on Verity Sense.

Current issue reports are **unverified reports**, not universal device facts: [#862](https://github.com/polarofficial/polar-ble-sdk/issues/862) offline entry date UTC-offset discrepancy; [#855](https://github.com/polarofficial/polar-ble-sdk/issues/855) trigger invalid-parameter errors; [#839](https://github.com/polarofficial/polar-ble-sdk/issues/839) long ACC files failing download; [#829](https://github.com/polarofficial/polar-ble-sdk/issues/829) Verity Sense firmware 3.0.16 notification enable failures. Check affected versions/status before native development. The known-issues guide also documents older PPG rate-report and charging-battery fixes.

## Windows and remaining experimental questions

Official SDK targets iOS/Android, not Windows. Bleak provides Windows WinRT support; see [Bleak troubleshooting](https://bleak.readthedocs.io/en/latest/troubleshooting.html). Use one asyncio event loop, pass discovered BLEDevice objects, serialize GATT operations, and avoid `input()` blocking that loop. Console apps need MTA; importing pywin32 can accidentally initialize STA. Use a clean venv and plain PowerShell if callbacks hang. Radio availability, drivers, permissions, other connections, charger state and wireless interference remain platform concerns.

**Observed experimentally:** only software and BLE scan evidence explicitly recorded in `validation.md`. No exercise classification or real sampling result may be inferred from synthetic fixtures.

**Assumptions / unknown until hardware test:** this sensor's firmware, reported settings, simultaneous live/offline ACC + gyro reliability, concurrent HR quality, clock drift and reset behavior, label alignment delay, battery life, useful upper-arm signatures, rep visibility, orientation sensitivity and false positives during daily activity.
