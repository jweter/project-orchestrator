# iPhone Native & Offline-First Product Portfolio — Five New Project Tracks

**Planning date:** 2026-11-01  
**Status:** owner requested planning; documents are separate project proposals, not merged changes to existing product roadmaps, new repo creations, runtime releases, or implementation authorization beyond the owner's standing product scope.

## Product boundary

**Five apps, seven source repositories:**

1. **Iron God Pocket** — native offline engineering/evidence review and planning, with optional authenticated remote control of Azathoth.
2. **VoiceStudio Pocket** — local recording/editing/transcription/system TTS; custom on-device voice inference only after feasibility proof.
3. **Knowledge Engine Pocket** — one standalone research app for Core+AI+Web: PDFs/OCR/local citations/evidence notebook; on-demand online discovery optional.
4. **Rocksmith Pocket Workshop** — offline tab/audio/arrangement editing with honest portable exports; Rocksmith-game packaging stays a separate feasibility gate.
5. **Everward Mobile** — full Unreal/C++ offline game sharing the authoritative One Probe simulation, pending Mac/Xcode signed iOS build and real device performance tests.

## Three product classes

- **True offline-native MVP possible now with targeted Swift/iOS work:** Iron God evidence viewer, VoiceStudio recorder/editor, Knowledge Engine saved source notebook, Rocksmith tab workstation.
- **True offline-native game possible with dedicated iOS build/rendering work:** Everward. It needs Unreal's native mobile target and Mac/Xcode for signed C++ builds.
- **Full desktop feature parity not yet demonstrated on iPhone:** unattended Iron God desktop workers, large VoiceStudio voice-cloning models, unlimited Knowledge Engine fresh web research without internet, Windows DLC Builder final packaging. Do not claim these in MVP marketing.

## Phased gates and suggested portfolio priority

| Rank | Project | First independently verifiable demonstration | Highest-risk research |
| --- | --- | --- | --- |
| 1 | Iron God Pocket | Airplane-mode import/inspect/export of a real signed evidence snapshot | Remote owner-control authentication and authoritative receipt mapping |
| 2 | VoiceStudio Pocket | Offline recording, trim, playback/export and availability-gated STT | On-device custom voice cloning quality and model resources |
| 3 | Knowledge Engine Pocket | Two PDFs → FTS search → exact anchored citations → offline report | Portable evidence provenance and memory/storage budgets |
| 4 | Rocksmith Pocket Workshop | Audio import → waveform/grid → bass/lead/rhythm edit → MIDI/MusicXML export | Fully native valid PSARC construction/in-game verification |
| 5 | Everward Mobile | iPhone Unreal C++ simulation → probe movement/scan → local save | Mac/Xcode/UE compatibility, GPU thermals and battery |

These are **suggested sequence priorities, not deadlines**. Small contract/spike work can proceed in parallel only when it does not disrupt authorized mainline priorities or hardware/worker capacity. Individual roadmaps define each exact stage.

## Cross-project infrastructure, shared rather than duplicated

- **Native build/release:** choose supported minimum iOS and macOS/Xcode per physical device test; use isolated CI or owner-controlled compatible Mac. No hardware/purchase authorization from this planning document. Apple Developer membership is not required for local development on personal devices but is for App Store/TestFlight distribution. Confirm requirements at the time of launch.
- **Offline verification:** scripted airplane mode, local storage durability, no unexpected network traffic, on-device evidence collection, storage/memory/thermal tests, app background/foreground recovery.
- **Portable contracts:** signed/digested snapshots for Iron God, speech/audio metadata for VoiceStudio, Core provenance/evidence bundles for Knowledge Engine, validated note/timing/source manifests for Rocksmith, deterministic simulation/save format for Everward.
- **Security:** opt-in connectivity, Keychain secrets, explicit remote-control authority, protected content and microphone permissions, user rights, verifiable citations, minimal telemetry.
- **Worker/process:** iPhone apps are user experiences; they do not create a competing Iron God supervisor. Active Iron God release is not changed to implement them.
- **Planning governance:** keep this portfolio track separate from current owner-approved production roadmaps. After review, reconcile new native-specific capabilities into appropriate roadmap/capability graphs without auto-bypassing any product or owner gates.

## External design constraints checked

- Apple Core ML: https://developer.apple.com/documentation/coreml
- On-device transcription availability: https://developer.apple.com/documentation/speech/sfspeechrecognizer/supportsondevicerecognition
- On-device Vision OCR: https://developer.apple.com/documentation/vision/recognizing-text-in-images
- iOS Background Tasks and controlled long processing: https://developer.apple.com/documentation/backgroundtasks/choosing-background-strategies-for-your-app and https://developer.apple.com/documentation/backgroundtasks/performing-long-running-tasks-on-ios-and-ipados/
- Unreal Engine 5.8 iOS/SDK requirements: https://dev.epicgames.com/documentation/unreal-engine/ios-ipados-and-tvos-development-requirements-for-unreal-engine
- Apple Developer membership: https://developer.apple.com/help/account/membership/programs-overview

## Definition of portfolio completion

Each app must be independently installed and used on a supported iPhone with Azathoth and all remote servers turned off for its explicitly scoped core user journey. If a feature cannot run offline, its UI and documentation must call it optional, unavailable, or experimental—not silently trigger a remote job or claim full device autonomy. Every project gets an owner-reviewed charter, phase-gated roadmap, risk matrix, exact-head test plan and tested physical-device result before public release.