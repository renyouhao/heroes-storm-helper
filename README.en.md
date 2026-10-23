[简体中文](README.md) | English

# Heroes of the Storm Helper

A Windows companion for Heroes of the Storm: read incoming English and Korean chat in Chinese, translate your Chinese replies into English or Korean, and use optional helpers for loot chests, repeated key presses, favorite talents, and team callouts.

**Release version: v1.3 · Application/package version: 1.3.0.** The links below point to the corresponding complete Windows package and release notes. The application is distributed as **风暴英雄助手** and uses a Chinese interface.

**Supported validation target: Windows x64, Chinese game client build 98348.** This English introduction does not mean the English game client has been validated. Game updates require another compatibility check.

## Download

**[Download the Windows x64 ZIP](https://github.com/renyouhao/heroes-storm-helper/releases/download/v1.3/HotsHelper-1.3.0-Windows-x64.zip)** · [v1.3 release notes and assets](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v1.3) · [All releases](https://github.com/renyouhao/heroes-storm-helper/releases)

Download `HotsHelper-1.3.0-Windows-x64.zip`, extract the entire archive, and run `风暴英雄助手.exe`. The directory and executable inside the ZIP retain their Chinese names. Keep the `_internal` folder alongside the executable. Python and separate SDK installation are not required for the packaged application.

GitHub's **Source code (zip/tar.gz)** downloads contain the source rather than the ready-to-run Windows application. When switching packages, exit the old helper before launching the new copy.

Version note: `v1.1` remains a source-preparation tag; its Release was never published. During release preparation, GitHub stripped Chinese characters from the asset name. The tag is preserved, and the asset naming contract is corrected in `v1.2`, using an ASCII ZIP name shared by the publisher and updater. **Existing local 1.1.0 packages recognize only Chinese ZIP names, so download v1.3 manually using the link above.** Your local user data stays in place.

## Changes in v1.3

- **Clearer desktop controls.** Neutral Notion-inspired colors and compact navigation remain, with Codex-inspired rounded buttons, fields, and cards. Selected settings show a real **✓**. Ctrl+T stays dark and readable; Enter translates/copies, Esc closes, and Ctrl+Tab switches language. The helper uses its own hexagonal storm emblem with a chat bubble at the lower right.
- **Explicit translation states.** Chinese messages and messages containing only numbers, symbols, or emotes show only their original text, without an unsupported, unnecessary, or untranslated label. English and Korean still translate normally, with queued, translating, or failed states. Missing configuration, disabled cloud translation, paused automatic translation, and genuinely unsupported languages retain their reasons. Failure details and manual retry remain available.
- **Reading time for late subtitles.** A late translation admitted within the default 15-second freshness window gets at least 3 seconds of actual reading time; new original text does not immediately replace it. Longer display settings extend the bounded freshness window. Expired content stays retired, changing matches or disabling subtitles clears it, and hiding subtitles during Ctrl+T does not extend their deadline.
- **Visible errors and recovery.** Blocked/unconfirmed talent selection or helper errors can produce a brief passive notice while the game is foreground. Notices pass mouse input through and do not take focus; successful operations stay quiet. Saved enabled preferences and actual worker state appear separately, with **重新连接** (“Reconnect”) after an unexpected stop. Reconnection does not replay old actions.
- **Less repeated painting.** Following the latest chat updates only changed rows. Rounded controls share a fixed image pool and cards reuse a single shape; theme/DPI changes update existing resources without animation or extra polling.

An earlier offline comparison with 500 synthetic messages measured about **65% lower median append/translation repaint time**. This is the previous controlled measurement, not an in-game frame-rate result. The earlier whole-window offline measurement was roughly **60 MiB private memory / 90 MiB working set**, without live game/cloud load; it does not establish resource use on every machine or in frozen packages. Real-match CPU, memory, and input behavior still need validation.

## Features

- **Incoming chat translation.** Receive team, party, whisper, and all-player chat, with separate lobby and match capture. English and Korean messages can be translated into Chinese. Match-source filtering and bounded rediscovery help avoid staying attached to an old match.
- **Quick replies with `Ctrl+T`.** Enter Chinese, choose English or Korean, translate, and copy the result. You return to the game and paste/send it yourself.
- **One-click setup with your own Aliyun key.** Enter your AccessKey ID and Secret, then connect once to verify the account, test translation, save encrypted credentials, and enable translation.
- **Subtitles and a phrasebook.** Adjustable transparent subtitles, reusable phrases, and three color themes shared by the main window and overlays.
- **Loot chest helper.** Prepare it in the helper, switch to the game's loot chest screen, and use Space to start, pause, or resume. Esc ends the session.
- **Configurable key repetition.** Choose `S` or alternating `Y+S`; each mode has its own saved interval of 20–5000 ms, with 20 ms as the default for new settings. `F8` starts/stops repetition; its shortcut can be changed to another F1–F12 key.
- **Automatic favorite talents — off by default.** Uses the game's existing favorite selection through its shortcut, then reads back whether that tier has been selected. It needs no translation key.
- **Tab double-click callouts — off by default.** Double-click one selected talent or one supported statistics cell to send that item's information to your team. Talent summaries are bundled offline; statistics callouts report only the clicked metric, without rankings or performance judgments. No translation key is required.
- **Manual update checks.** Check the official repository's stable releases, download and verify the package, then choose to restart and update. The updater supports `HotsHelper-<version>-Windows-x64.zip` assets. There is no automatic startup update check or downgrade; v1.3 and application version 1.3.0 represent the same version. Older local 1.1.0 packages require a manual download because their updater only recognizes Chinese asset names.
- **Emote handling and optional diagnostics.** Complete image-emote tags display as `[表情]`; emote-only messages skip cloud translation. Diagnostics are off by default and can be enabled for troubleshooting.

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

Chat boxes, menus, loot actions, repetition, quick input, and update preparation can block a callout. Unknown or untrusted data is not sent. Attempts are limited to once per item within 3 seconds and at least 2 seconds between items; toggling the feature does not reset those limits. A failed attempt is not automatically retried. If text remains in chat, inspect the reason on the helper's **双击播报** page and press Esc to clear the draft before trying again. If the status says the message may already have been sent, check chat history first.

The lobby/match label at the bottom of the main window describes the chat capture source, not the callout result. Callout status appears on its own page. In v1.3, critical errors also get a passive foreground-game notice. If an enabled helper reports that its worker stopped, use **重新连接** (“Reconnect”) on that helper’s page; this restores listening without resending failed messages or repeating a talent action for the same tier. Leaving a match invalidates an old pending callout even if Tab remained visible; open the panel and double-click again in the new match.

## Local data and privacy

User data lives in `%LOCALAPPDATA%\HotsChatHelper`. Existing preferences, phrasebook data, and encrypted credentials retain this location across upgrades.

- Keys are encrypted with **Windows DPAPI**, bound to the current Windows user. Use your own key; credentials and user databases are not part of the public distribution.
- Normal startup does not read the project's `.env`. A legacy file is read only when you explicitly choose **导入本机旧 .env** (“Import a local legacy .env”).
- Old `usage.sqlite3` and `usage.sqlite3.initialized` files are left untouched and no longer read or created. A missing or corrupt old ledger does not block setup or translation.
- Translation text is sent to Aliyun; the sender's nickname is not included in that translation text. The visible chat history keeps the latest 500 messages for the current session and clears when the helper exits.
- **Diagnostics are off by default:** no diagnostic writer thread or log file is created until you enable them. Basic diagnostics record bounded events and identifiers without chat bodies. Optional detailed mode records limited text for 10 minutes; configured full keys and secrets remain redacted. Review other personal text before sharing an export.
- Diagnostic exports contain logs and a safe summary, exclude credential files, databases, and preferences, and are not automatically uploaded. Exporting does not call the cloud translation service.

## Run and develop from source

Use Windows x64 and **Python 3.10+**. Packaged builds use Python 3.12. From the repository root in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m hots_chat.app --demo
```

Demo mode is offline: it does not read the game or real credentials and does not call cloud services. Omit `--demo` for normal operation with your own key. The repository also provides `安装依赖.ps1` and `启动助手.ps1` as setup and launch shortcuts.

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
.\.venv\Scripts\python.exe -m tools.build_release
```

The build output is `releases/HotsHelper-1.3.0-Windows-x64.zip`. The outer asset name uses ASCII for GitHub and updater compatibility; the ZIP's directory and `风暴英雄助手.exe` keep their Chinese names. The build stages public seed data explicitly and rejects local credentials, databases, preferences, logs, and sampling files from the release bundle.

## Validation scope

The earlier unified source at commit `51daf8e` (merged as `69ec5a3`) passed **1,247 tests across 113 isolated test modules, plus 20 acquisition probes**. These figures describe the earlier verification baseline, not the new v1.2 test pass. See the [v1.2 release](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v1.2) for current test counts, artifact checks, source commit, and SHA256. The **interface-refactor baseline** passed **1,336 regression tests and 5 dedicated icon tests**. The earlier 20 acquisition probes remain the existing acquisition-validation baseline. Final regression results for the new Chinese original-text display fix, frozen-package installation checks, the build commit, and checksums belong to the [v1.3 Release record](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v1.3); source tests do not replace final-package acceptance. Source-test results alone do not establish that every in-game scenario or update path has been exercised.

Local checks target Chinese client build 98348. Talent and statistics callouts have been confirmed in team chat in an earlier fix package, and favorite-talent checks include selected heroes and special talent tiers. The following still need broader manual validation:

- Natural talent progression across successive normal and Brawl matches, including uninterrupted multi-match runs.
- The English game client, other client builds, and broader resolution/window-mode combinations.
- End-to-end input timing and game acceptance of repeated actions on different machines.
- Cross-version replacement between real frozen application packages. The update transaction has temporary-directory tests for replacement and rollback; startup checking only confirms short-term process survival.

Talent readback confirms that a tier became selected; it does not independently verify the current favorite target. Polling intervals are scheduling targets, not same-frame guarantees. Unknown game builds, identities, or modes do not authorize automatic talent selection or callouts. Spectator and replay use have not been validated.

For fuller behavior notes, troubleshooting steps, and historical validation details, use the [Chinese README](README.md) and [Chinese HTML guide](artifacts/风暴英雄助手新手指南.html).
