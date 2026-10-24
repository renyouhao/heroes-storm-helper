[简体中文](README.md) | English

# Heroes of the Storm Helper

A Windows companion for Heroes of the Storm: read incoming English and Korean chat in Chinese, translate your Chinese replies into English or Korean, and use optional helpers for loot chests, repeated key presses, favorite talents, and team callouts.

**Source target: v2.0 · Application/package version: 2.0.0 · Configured channel: `stable`.** Downloadable versions, assets and checksums are determined by the actual Release. The application is distributed as **风暴英雄助手** and uses a Chinese interface. Existing credentials, phrasebook data and preferences remain in place; previous theme choices migrate to Soft Lavender.

**New in v2.0:** the React + WebView2 desktop, a single Soft Lavender theme and purple hexagonal swirl icon, the existing option-B overlay layout and controls, unified 100-MiB diagnostic recording/replay, and all model-decoder branches for the supported game build.

**Supported validation target: Windows x64, Chinese game client build 98348.** This English introduction does not mean the English game client has been validated. Game updates require another compatibility check.

Build 98348 supports **all 16 model-decoder branches, 0–15**, with a verified compute-code fingerprint for each. Branches 2, 5, 8, 11 and 14 additionally require a runtime salt. Reads recheck the selector, table root, encoded slot, used keys, and salt pointer/value before trusting the result. All algorithms passed synthetic reads and independent native-instruction comparisons; this does not mean all 16 branches were exercised on affected players’ machines or that newer game builds are supported.

## Download

**[View published Windows x64 ZIPs](https://github.com/renyouhao/heroes-storm-helper/releases)** · [Target v2.0 release entry](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v2.0). The v2.0 asset name is `HotsHelper-2.0.0-Windows-x64.zip`; available downloads and SHA256 values are determined by the actual Release.

Download `HotsHelper-2.0.0-Windows-x64.zip`, extract the entire archive, and run `风暴英雄助手.exe`. The directory and executable inside the ZIP retain their Chinese names. Keep the `_internal` folder alongside the executable. Python and separate SDK installation are not required for the packaged application.

GitHub's **Source code (zip/tar.gz)** downloads contain the source rather than the ready-to-run Windows application. When switching packages, exit the old helper before launching the new copy.

Version note: `v1.1` remains a source-preparation tag; its Release was never published. During release preparation, GitHub stripped Chinese characters from the asset name. The tag is preserved, and the asset naming contract is corrected in `v1.2`, using an ASCII ZIP name shared by the publisher and updater. **Existing local 1.1.0 packages recognize only Chinese ZIP names, so download v2.0 manually using the link above.** Your local user data stays in place.

## Features

- **Incoming chat translation.** Receive team, party, whisper, and all-player chat, with separate lobby and match capture. English and Korean messages can be translated into Chinese. Match-source filtering and bounded rediscovery help avoid staying attached to an old match.
- **Quick replies with `Ctrl+T`.** Enter Chinese, choose English or Korean, translate, and copy the result. You return to the game and paste/send it yourself.
- **One-click setup with your own Aliyun key.** Enter your AccessKey ID and Secret, then connect once to verify the account, test translation, save encrypted credentials, and enable translation.
- **Subtitles, a phrasebook, and one Soft Lavender theme.** v2.0 uses Soft Lavender throughout the main window, settings and in-game overlays, with no theme selector. Existing theme preferences migrate automatically while preserving window positions, fonts and shortcuts. Transparent subtitles keep white text with a dark outline.
- **Loot chest helper.** Prepare it in the helper, switch to the game's loot chest screen, and use Space to start, pause, or resume. Esc ends the session.
- **Configurable key repetition.** Choose `S` or alternating `Y+S`; each mode has its own saved interval of 20–5000 ms, with 20 ms as the default for new settings. `F8` starts/stops repetition; its shortcut can be changed to another F1–F12 key.
- **Automatic favorite talents — off by default.** Uses the game's existing favorite selection through its shortcut, then reads back whether that tier has been selected. It needs no translation key.
- **Tab double-click callouts — off by default.** Selected talent callouts begin with “注意” (“Attention”) and include verified quest progress in the same cell. Standalone baseline quests and verified positive numbers from the dedicated Tab cooldown label for allied heroics use their own icons; statistics callouts report only the clicked metric. Talent summaries and known quest names are bundled offline. No translation key is required.
- **Manual update checks.** Check the official repository's stable releases, download and verify the package, then choose to restart and update. The updater supports `HotsHelper-<version>-Windows-x64.zip` assets. There is no automatic startup update check or downgrade; v2.0 and application version 2.0.0 represent the same version. Older local 1.1.0 packages require a manual download because their updater only recognizes Chinese asset names.
- **Emote handling and optional diagnostics.** Complete image-emote tags display as `[表情]`; emote-only messages skip cloud translation. Diagnostics are off by default and can be enabled for troubleshooting.

## Interface and navigation

**The v2.0 build uses the desktop interface below.** Keep the complete Windows ZIP contents; source changes do not update an older executable.

The six main pages use **React + TypeScript + CSS**, loading bundled local files through **pywebview + Microsoft Edge WebView2** without a development server or browser tab. Python continues to handle capture, translation and game input. In-game subtitles, the `Ctrl+T` input bar, and settings/credentials/diagnostics/update dialogs still use native Tk windows.

The main window follows the selected light-purple prototype, with a lavender sidebar, white content cards, and purple primary actions. Icon navigation opens chat, phrases, loot assistance, key repetition, talent assistance, and double-click callouts. Settings and update checks sit at the bottom of the sidebar. Soft Lavender is the only theme; older palette settings normalize automatically while preserving window positions, fonts and shortcuts. The EXE, taskbar and window titles share the purple hexagonal swirl mark.

Chat rows separate the nickname, channel, time, message, and translation. Circular avatars contain nickname text as placeholders; they do not identify the player's actual hero. Chinese input and translation preview appear side by side, with English/Korean pill choices for the target language. Translate, copy the result, and paste/send it yourself in the game. The separate `Ctrl+T` input remains available.

Small windows automatically reduce spacing and supporting text and rearrange navigation and actions while retaining input, preview, and language choices. In-game subtitles and the quick-input bar remain separate.

The new desktop UI requires [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/). If it is missing, follow the startup message to install the official runtime; the app does not silently switch to an older browser backend. Complete built packages do not require Node.js. Source users can add `-Legacy` to the launch script for the original Tk UI. Memory and CPU must be measured again across the full process tree; old Tk measurements do not establish WebView2 performance or a sub-100-MiB memory footprint.

### Overlay option B adopted; A/B preview retained

**Option B · 原样换配色 is selected for this change.** Soft Lavender overlays use colors matching the main window, keeping the existing subtitle layout, single-line input, language dropdown and keyboard behavior. Transparent subtitles keep white text with a dark outline. Quick input uses the main window's purple hexagonal swirl mark beside its title and preserves two hint lines: “输入中文，译文会复制；请在游戏中粘贴发送。” and “Enter 翻译并复制 · Esc 收起 · Ctrl+Tab 切换语言”. Option B keeps its layout and behavior while this change unifies the theme and global icon. Release builds check WebView2 page loading and offline bridging; in-game display, focus, input and cross-DPI behavior still require machine-specific validation.

You can also double-click `打开悬浮窗对比.cmd` in the repository root: it opens option B's quick-input preview without PowerShell or a persistent command window. Alternatively, run `预览悬浮窗AB.ps1` to open the comparison console. Switch “A · 原型新样式” / “B · 原样换配色” and “默认字幕” / “消息面板” / “快捷输入”; use “切换浅色背景”, “下一条消息” and “恢复位置” to compare backgrounds, advance messages and reset positions. **A**, with its two-column input, rounded corners and pale purple/white panels, remains an alternative preview. Switching previews does not change production preferences.

```powershell
.\预览悬浮窗AB.ps1
.\预览悬浮窗AB.ps1 -方案 B -状态 input
```

`-方案` accepts `A` or `B`; `-状态` accepts `subtitle`, `panel` or `input`, defaulting to `B` / `input`. Both previews use Tk and the existing project virtual environment, without npm, builds or network access. They do not read the game, credentials or preferences, call cloud services, or register a global `Ctrl+T`; the shortcut works only inside the preview. Select a preset Chinese phrase from the dropdown and click “填入输入条” to try its offline English/Korean results; other input prompts you to select a demo phrase. Copy and translate/copy actions replace the clipboard contents. The preview compares appearance and interaction; it does not establish in-game display, focus or input behavior.

## Getting started

1. Download and extract the complete Windows package above. Borderless windowed game mode is recommended.
2. Enable **Aliyun Machine Translation General Edition** on your own account. Create a RAM user and AccessKey with the `alimt:TranslateGeneral` permission.
3. Open the helper's cloud configuration, enter your AccessKey ID and Secret, and click **连接并开始使用** (“Connect and start using”). The helper verifies identity, translates `你好` as a test, and saves the key with Windows DPAPI only after success.
4. Start the game. Select the desired chat channels in the helper and check incoming translations. For outgoing replies, press `Ctrl+T`, translate your text, copy it, and paste/send it in the game.

You can skip cloud configuration and use original-text display, loot assistance, and key repetition. Cloud translation requires an internet connection and is billed to your own Aliyun account. Identity verification alone does not establish that translation service access, permissions, and billing are ready; setup also runs a translation test.

There is **no local monthly usage ledger, monthly budget confirmation, or local monthly character cap**. Request queues, short-term automatic-translation rate limits, caching, deduplication, and pause controls remain in place.

To check remaining quota, usage, or charges, click **查询剩余额度** (“Check remaining quota”) in the main window or under **设置 → 连接 → 翻译服务**. This opens the [Aliyun Machine Translation console](https://mt.console.aliyun.com/service) in your default browser. Log in there to inspect your account; the helper provides a link and does not query account usage automatically.

For detailed setup instructions and troubleshooting, see the [Chinese getting-started guide](artifacts/风暴英雄助手新手指南.html). Download the HTML file to open it offline; the Windows package also includes it.

## Using the optional game helpers

**Key repetition:** choose a mode and interval, save/apply it, return to the game, and press `F8`. Saving settings ends the current action rather than starting a new one. Esc, Enter, or leaving the game ends repetition; it does not automatically resume when you return. These are literal key presses, so an open chat box can receive `S` or `Y`. Actual timing depends on Windows scheduling and the game's response.

**Favorite talents:** first mark your preferred talents in the game and keep its quick-favorite shortcut set to `Ctrl` + grave accent (the key below Esc). Enable **天赋辅助** in the helper. It reads the current hero's actual talent tiers, including special unlock levels, and defers while another helper owns input. Both the feature and its saved preference start disabled for a new user.

**Double-click callouts:** enable **双击播报**, return to the game, and open Tab. Double-click one selected talent or statistics cell, then release the mouse and other keys while keeping the game foreground. Keep the game's `Ctrl+Enter` team-chat shortcut at its default. The helper checks the target, team channel, and empty chat box, then reads the entered text back before submitting. It does not change the clipboard or replace a user draft.

Use Tab's **talent page** for talents, baseline quests and heroics. Talent messages begin with “注意” and retain the hero's actual unlock level, name and short effect. A verified quest in that same selected cell adds its current count, including a real zero, or reports completion. Both allied and enemy quests are supported when their native bindings and displayed state are trustworthy. Standalone baseline quest names resolve by exact hero/quest ID: the offline directory contains 16 entries with explicit Face and localization evidence. A missing name becomes “基础任务1/2” only after the native identity and value checks still pass. Hidden, untracked or unknown counters are not treated as zero, and stale hidden labels are not used.

Double-click an **allied heroic icon** to report a visible, verified positive number from its dedicated Tab cooldown label, using “冷却显示：X秒” (“Cooldown display: X seconds”). Enemy heroic cooldowns are not read. The engine chooses this display from two timing channels, and it may show charge recovery. It does not establish that the heroic is currently unusable, has no charges left, or will become castable after that time; charges, resets and variants can affect availability. Zero, blank or hidden labels never imply “ready”, and remaining charges are not guessed. Tracer’s dedicated charge UI remains unverified and unsupported; unresolved abilities or changing bindings produce no callout. This uses the game's common UI structure rather than saved addresses from one machine. Build/layout/identity mismatches remain unknown. The 16 model decoders locate player models; quest and cooldown display checks are additional stages.

Live read-only comparisons in this round cover Kael'thas Convection at `0 → 2`, an enemy Arthas baseline quest at `6 → 8`, and allied heroic displays of `38/29` seconds. These are observed samples, not live acceptance of every hero, language, resolution or final chat submission. The name-directory count is not a count of heroes tested in game.

Chat boxes, menus, loot actions, repetition, quick input, and update preparation can block a callout. Unknown or untrusted data is not sent. Attempts are limited to once per item within 3 seconds and at least 2 seconds between items; toggling the feature does not reset those limits. A failed attempt is not automatically retried. If text remains in chat, inspect the reason on the helper's **双击播报** page and press Esc to clear the draft before trying again. If the status says the message may already have been sent, check chat history first.

The lobby/match label at the bottom of the main window describes the chat capture source, not the callout result. Callout status appears on its own page. Leaving a match invalidates an old pending callout even if Tab remained visible; open the panel and double-click again in the new match.

## Local data and privacy

User data lives in `%LOCALAPPDATA%\HotsChatHelper`. Existing preferences, phrasebook data, and encrypted credentials retain this location across upgrades.

- Keys are encrypted with **Windows DPAPI**, bound to the current Windows user. Use your own key; credentials and user databases are not part of the public distribution.
- Normal startup does not read the project's `.env`. A legacy file is read only when you explicitly choose **导入本机旧 .env** (“Import a local legacy .env”).
- Old `usage.sqlite3` and `usage.sqlite3.initialized` files are left untouched and no longer read or created. A missing or corrupt old ledger does not block setup or translation.
- Translation text is sent to Aliyun; the sender's nickname is not included in that translation text. The visible chat history keeps the latest 500 messages for the current session and clears when the helper exits.
- **Diagnostics are off by default:** no writer thread or log file is created until enabled. The single switch records chat originals, translations and replay inputs/states for translation, automatic talents and double-click broadcasts. There is no separate detailed mode or ten-minute expiry. Logs default to a total cap of 100 MiB, rotating the oldest files and retaining up to seven days. Configured full keys/secrets are recursively redacted; review chat privacy before sharing an export.

For automatic talents and Tab double-click broadcasts failing together, use a new build containing replay recording, keep the original client language/resolution/scaling, and enable diagnostics before entering a normal match. Enable both helpers, note when an automatic selection fails, double-click one selected talent and one statistic in Tab, then export the ZIP. For translation failures, reproduce incoming chat or manual translation while recording. Include approximate times, game build, match/window mode, Windows scaling and monitor count. An existing EXE does not gain these events from a source update.

The new `helper.read` events distinguish `source=talent` and `source=broadcast`. They record actual/expected game build, connection/identity/talent/board/coordinate stages, fixed failure reasons, exception types and available Win32 error codes. `identity_reason` explains native identity rejection without exposing pointers or decoded state. Broadcast reads also record client size and desktop origin, render size, coordinate rejection reason, verified raw talent/stat cell counts, rejected metadata/coordinates, final hit count, read calls/bytes and elapsed time. Fields appear only when observed in that sample; missing render dimensions after an early failure are unknown, not zero or cached values.

`helper.click` records each sampled left-button press, click position, unique/ambiguous/no hit, hit kind and queue age. `attempt_id` links that press to later validation within the broadcast helper; it is not a double-click gesture ID. Chat/menu/foreground states retain `true`, `false` or `unknown`. Changed fixed cancellation reasons produce new status events. Replay records preserve operation order and state; diagnostic failures must not stop game helpers. Summary fields omit raw memory addresses; decoder replay includes encoded words, key/salt values and the computed pointer, rather than a full memory dump. Credential files are excluded, and exports include chat/replay content.

`board_profile=98348-quest-cd-1` identifies the quest/cooldown field profile. `board_code_verified=1` means code checks passed for six native getters: talent quest state/value, baseline quest index/state/value, and HeroicAbilLink. A zero means verification has not passed; changed code leaves the result unknown instead of trusting the same field offsets.

Quest events include `quest_raw_state/quest_raw_progress`, the interpreted `quest_state/quest_progress`, and `progress_reason`. Allied heroic events include `cooldown_state/cooldown_seconds/cooldown_reason`. The same optional 100-MiB recording includes these observations, chat originals and translations. Unknown or unobserved values remain unknown.

Both features share the supported-build check (currently 98348), process/module access, native local-player identity and hero/talent catalog reads. Matching failures at `connect` or `identity` indicate a possible shared cause. Automatic talents do not use Tab pixel coordinates, so a 2K coordinate conversion problem alone does not explain both failures. English-client compatibility still needs live validation; added diagnostics locate failures rather than claim to fix them.

Offline replay: run `python -B -m tools.replay_diagnostics <diagnostic.zip>` from the development environment. The default JSON report is written beside the ZIP; use `--component translation/talent/board` or `--session <id>` to filter. The tool streams known JSON logs without extracting/running archive code, calling cloud APIs, opening a game or sending input. `replay.frame` entries feed recorded boundary inputs and state to actual business code and compare results. Recorded cloud/Windows/game responses are simulated inputs, not a reproduction of those external systems. Missing frames, rotation, truncation, redaction and write failures are reported as incomplete; legacy logs without frames cannot be replayed. A matching replay does not mean the recorded failure is fixed.

Use `--component reader` for decoder guards, pointer arithmetic, and quest/cooldown display decisions. Recorded `quest_progress` and `cooldown_display` frames replay their observed values, display state and required checks through the same parsing functions, without reconstructing the entire live UI.

Build 98348 supports model-decoder branches 0–15, with a compute-code fingerprint check for every branch. Branches 2, 5, 8, 11 and 14 require a valid runtime salt; missing values are never replaced with zero. A `decode_pointer` frame records encoded words, two ordered key reads, the salt used when required, and the computed pointer. Its `computed` outcome validates arithmetic only; later model validation can still reject a changed selector, root, slot, key or salt pointer/value. All 16 algorithms passed synthetic reads and 303,810 independent native-instruction comparisons. These are offline results, not live acceptance of every branch on affected players’ machines; English/2K and match-specific behavior still need live validation. A selector is not a fixed user property, and current observations do not establish whether it changes on each match or restart. Historical expected rejections may differ after new codec support; old logs without encoded words, keys or required salt cannot reconstruct those computations.
- Diagnostic exports contain logs and a safe summary, exclude credential files, databases, and preferences, and are not automatically uploaded. Exporting does not call the cloud translation service.

## Run and develop from source

Use Windows x64, **Python 3.10+**, **Node.js LTS with npm**, and WebView2 Runtime. Packaged builds use Python 3.12; Node.js is needed only for source setup and frontend builds. From the repository root in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
Push-Location frontend
npm.cmd ci
npm.cmd run build
Pop-Location
.\.venv\Scripts\python.exe -m hots_chat.app --demo
```

Demo mode is offline: it does not read the game or real credentials and does not call cloud services. Omit `--demo` for normal operation with your own key. The repository also provides `安装依赖.ps1` and `启动助手.ps1` as setup and launch shortcuts. Setup verifies Node.js/npm and builds `frontend/dist`. Launch loads those local files without starting Vite. Run setup again if resources are missing. Use `启动助手.ps1 -Legacy -Demo` (or module flag `--legacy-ui`) for the original Tk compatibility UI.

The Python distribution is named `hots-assistant`; source imports retain the `hots_chat` module name.

Tests and build checks:

```powershell
Get-ChildItem tests/test_*.py | ForEach-Object {
    .\.venv\Scripts\python.exe -m unittest ("tests." + $_.BaseName) -q
    if ($LASTEXITCODE -ne 0) { throw "Test failed: $($_.Name)" }
}
.\.venv\Scripts\python.exe -m unittest discover -s probes -p "test_*.py" -v
.\.venv\Scripts\python.exe -m compileall -q src tests tools probes packaging
.\.venv\Scripts\python.exe -m pip check
```

The recorded full-suite verification runs test modules in isolated processes. Tests use synthetic data, controlled offline services, and temporary user directories; Windows UI integration tests create their own hidden Tk windows.

To build a Windows package:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m tools.build_release --output "releases/v2.0"
```

The build first runs `npm ci` and `npm run build`, checks the frontend output, then freezes Python with PyInstaller. It includes `frontend/dist`, the Windows WebView2 bridge, and pythonnet dynamic libraries; it does not bundle Node.js, a development server, or Qt/CEF browser backends. The target computer needs WebView2 Runtime.

The command above writes `releases/v2.0/HotsHelper-2.0.0-Windows-x64.zip`. The ZIP’s directory and `风暴英雄助手.exe` retain their Chinese names. The internal version is `2.0.0`, the channel is `stable`, and the GitHub release tag is `v2.0`. Running the local build command alone does not create a Git tag or public Release. The build explicitly stages public seed data and bundled UI files and rejects local credentials, databases, preferences, logs and sampling files from the release bundle.

Packaging runs `--check-installation` for native operations and seed data, `--check-desktop-installation` for desktop resources and dependencies, and `--check-desktop-rendering` to load the local WebView2 page and verify the React demo state and JavaScript bridge. All three must pass before creating the ZIP; the manifest records each result. The bundled “查看界面效果.cmd” starts an offline preview directly. These checks confirm loading and bridging; visual similarity, DPI behavior and in-game behavior still require separate validation.

Old packages are cleaned up only after the new package passes its installation check, every ZIP entry matches the manifest's size and SHA256, and `发行校验.json` is written successfully. For `releases/` and its release subdirectories, the current ZIP, manifest, and build directory are retained; only recognized older packages and build directories are removed. Unrelated files and source directories are preserved. A failed build or verification keeps old packages. An external `--output` only cleans recognized products inside that directory, never its parent; directories containing symbolic links or junctions are skipped.

## Validation scope

For v2.0’s final full-suite count, artifact checks, build commit and SHA256, see the [v2.0 Release record](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v2.0). The earlier unified source at commit `51daf8e` (merged as `69ec5a3`) passed **1,247 tests across 113 isolated test modules, plus 20 acquisition probes**. These figures describe the earlier verification baseline, not the new v1.2 test pass. See the [v1.2 release](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v1.2) for its historical test counts, artifact checks, source commit, and SHA256. Source-test results alone do not establish that every in-game scenario or update path has been exercised.

Local checks target Chinese client build 98348. Talent and statistics callouts have been confirmed in team chat in an earlier fix package, and favorite-talent checks include selected heroes and special talent tiers. The following still need broader manual validation:

- Natural talent progression across successive normal and Brawl matches, including uninterrupted multi-match runs.
- The English game client, other client builds, and broader resolution/window-mode combinations.
- End-to-end input timing and game acceptance of repeated actions on different machines.
- Cross-version replacement between real frozen application packages. The update transaction has temporary-directory tests for replacement and rollback; startup checking only confirms short-term process survival.

Talent readback confirms that a tier became selected; it does not independently verify the current favorite target. Polling intervals are scheduling targets, not same-frame guarantees. Unknown game builds, identities, or modes do not authorize automatic talent selection or callouts. Spectator and replay use have not been validated.

For fuller behavior notes, troubleshooting steps, and historical validation details, use the [Chinese README](README.md) and [Chinese HTML guide](artifacts/风暴英雄助手新手指南.html).
