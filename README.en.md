[简体中文](README.md) | English

# Heroes of the Storm Helper

A Windows companion for Heroes of the Storm: read incoming English and Korean chat in Chinese, translate your Chinese replies into English or Korean, and use optional helpers for loot chests, repeated key presses, favorite talents, and team callouts.

**Current source/local package target: v2.4 · Application/package version: 2.4.0 · Configured channel: `stable`.** The public download below remains **v2.3**; a local v2.4 build does not create a GitHub Release. Downloadable versions, assets and checksums are determined by the actual Release. The application is distributed as **风暴英雄助手** and uses a Chinese interface. Existing credentials, phrasebook data and preferences remain in place; previous theme choices migrate to Soft Lavender.

**Supported validation target: Windows x64, Chinese game client build 98348.** This English introduction does not mean the English game client has been validated. Game updates require another compatibility check.

## Download

The link below is the published **v2.3 binary package**, including update shutdown handoff, preparation-stage messages and automatic startup checks, plus the earlier startup and Brightwing fixes. **When upgrading from v2.1/v2.2, manually replace the full package.** A running old updater does not acquire the handoff fix from the new download.

The **local v2.4 build** includes untimestamped match-chat capture, known-format support for draft-team chat (【征召团队】), subtitle monitor selection, enhanced diagnostics and the complete Aliyun/DeepSeek service selection, API-key, balance and context controls. The earlier v2.3 match-diagnostics test build used the old baseline and did not include DeepSeek; the public v2.3 package does not include these additions either.

**[Download v2.3 for Windows x64](https://github.com/renyouhao/heroes-storm-helper/releases/download/v2.3/HotsHelper-2.3.0-Windows-x64.zip)** · [Release notes and SHA256](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v2.3) · [All versions](https://github.com/renyouhao/heroes-storm-helper/releases)

Download `HotsHelper-2.3.0-Windows-x64.zip`, extract the entire archive, and run `风暴英雄助手.exe`. The directory and executable inside the ZIP retain their Chinese names. Keep the `_internal` folder alongside the executable. Python and separate SDK installation are not required for the packaged application.

GitHub's **Source code (zip/tar.gz)** downloads contain the source rather than the ready-to-run Windows application. When switching packages, exit the old helper before launching the new copy.

**Older updater stuck on preparation with a disconnected page:** close the entire old helper, extract a new package containing the fix into a new directory, keep `_internal`, and run `风暴英雄助手.exe`. Credentials, phrases and preferences continue to use the existing user-data directory. The public v2.3 package contains this fix; download the complete ZIP above.

## Current interface

The screenshots below show the current v2.1 React interface. They use isolated offline sample data and do not demonstrate live game capture, successful cloud translation, or completed in-game actions. The application interface is Chinese.

<img src="docs/images/v2.1/01-chat.png" width="100%" alt="Current v2.1 chat page with offline sample messages, Chinese reply input, language choices, and translation preview">

*v2.1 current React components, isolated offline sample data; not live gameplay/cloud results.*

Read incoming chat above and write Chinese in the reply area below. Choose English or Korean, then translate and copy. Paste and send the result yourself in the game. If translation fails, the reply area keeps your input for correction or retry.

## Getting started

The two-service choice and onboarding below apply to **v2.4 source/local builds**. The public **v2.3 download above does not yet include them**.

1. Download and extract the complete Windows package above. Borderless windowed game mode is recommended.
2. As a new user, click **选择翻译服务** (“Choose translation service”) on the main page. The account page first shows Aliyun and DeepSeek cards with neither selected. Choosing one reveals its credential form. You can also skip setup and read original chat text.
3. Follow the [new-user or existing-user steps](#translation-service-configuration-and-switching) below, or open **偏好设置 → 连接 → 配置账户** (Preferences → Connection → Configure account). Verification must succeed before the helper activates the chosen account.
4. Start the game. Select the desired chat channels in the helper and check incoming translations. For your first reply, press `Ctrl+T`, enter Chinese, choose English or Korean, and press Enter to translate and copy. Return to the game, paste the result, check it, and send it yourself.

**Account setup — 个人翻译账户.** New users choose a service before seeing its credential fields. Existing users keep their current account and can switch after successful verification, as shown below.

<img src="docs/images/translation-service-choice.svg" width="100%" alt="Translation-service workflow: new users choose Aliyun or DeepSeek before entering credentials; existing users retain their account or activate another after verification">

*Workflow diagram, not a running-application screenshot.* It contains no real key and does not show a successful connection or verification. Use a new build containing these changes.

**Native quick input — 快捷中文输入.** `Ctrl+T` opens a separate native Tk input bar. The diagram below explains the flow; it is not a screenshot of that window or the game.

<img src="docs/images/v2.1/quick-input-flow.svg" width="100%" alt="Native quick-input flow: Ctrl+T opens input; type Chinese and choose English or Korean; Enter translates and copies; return to the game to paste and send manually">

*Workflow diagram, not a gameplay or cloud-result screenshot.* If you have saved favorites, a few appear when the input is empty; entering Chinese finds matching local phrases. Press Tab to enter the candidate list, use arrow keys there, then Tab again or click to fill the text. Filling does not translate or send it. Enter translates and copies; `Ctrl+Tab` changes language and Esc closes the bar. Candidate shortcuts leave IME composition alone. A reviewed, exact, unambiguous saved translation for the current language can be reused; other input still needs translation.

You can skip cloud configuration and use original-text display, loot assistance, and key repetition. Cloud translation requires an internet connection and is billed to your personal account with the active provider. Setup verifies service access and runs a translation test before saving and activating the account.

### Translation service configuration and switching

#### New users: choose a service, then connect

1. Click **选择翻译服务** (“Choose translation service”) on the main page, or open **偏好设置 → 连接 → 配置账户** (Preferences → Connection → Configure account). With no saved account, neither service card is selected by default. Choose Aliyun Machine Translation or DeepSeek V4.1 Flash to reveal its credential form.
2. **For Aliyun**, enable **Machine Translation General Edition** in the [Aliyun Machine Translation console](https://mt.console.aliyun.com/service), grant the RAM user `alimt:TranslateGeneral`, and fill the **two AccessKey ID / Secret fields** using the same key pair. **For DeepSeek**, create your own key on the official [API Key page](https://platform.deepseek.com/api_keys), then fill the **single API Key field** in the helper.
3. Click **连接并开始使用** (“Connect and start using”). Verification and the `你好` translation test must succeed before encrypted saving and activation. Selecting a card or entering credentials alone sends no request and does not activate the service.
4. You can skip setup, keep reading original chat text, and return through the main-page entry later.

#### Existing users: retain or switch accounts

1. Existing Aliyun accounts are reused automatically, including legacy DPAPI credentials. **No forced migration or key re-entry is required.** The account page initially selects the service actually in use.
2. To switch, choose another card under **要配置或使用的翻译服务** (“Service to configure or use”). This changes the form and clears the current credential input; it **sends no cloud request and does not immediately switch services**. **当前服务** (“Current service”) continues to show the active provider.
3. If that service has a saved account, click **使用已保存账户** (“Use saved account”); successful verification activates it without entering the key again. Otherwise, connect it using the new-user steps. The two accounts are encrypted separately and can be switched back at any time. Translation failures do not automatically fall back to the other provider. Deletion affects only the currently active account.
4. **Canceling unfinished setup leaves cloud translation paused.** To continue, return to the account page, choose the original service, and click **使用已保存账户** (“Use saved account”); successful verification enables it again. Closing the page does not undo saves or deletions that have already completed.

#### DeepSeek context settings

DeepSeek uses **`deepseek-flash` (V4.1 Flash)** with thinking disabled and only the current sentence's translation returned. **发送同会话最近 3 条原文** (“Send the last three originals from the same conversation”) is enabled by default and can be disabled. When enabled, it includes up to the three most recent anonymous originals from the same channel or whisper contact, without attaching nicknames; an unknown sender or contact receives no stitched history. For an active DeepSeek account, click **保存上下文设置** (“Save context settings”) to apply a change. Manual replies in the main window and `Ctrl+T` use only the current input and do not attach game-chat history.

There is **no local monthly usage ledger, monthly budget confirmation, or local monthly character cap**. Request queues, short-term automatic-translation rate limits, caching, deduplication, and pause controls remain in place.

- **Aliyun active:** click **查询免费额度 / 用量** (“Check free quota / usage”) under **偏好设置 → 连接** or on the account page to open the [Aliyun Machine Translation console](https://mt.console.aliyun.com/service) in your default browser. Log in there to inspect character quota, usage and charges. The helper provides this link without automatically reading Aliyun usage or opening the browser at startup.
- **DeepSeek active:** click **查询账户余额** (“Check account balance”) for a background query. The dialog shows the returned **total, granted and topped-up balances** for CNY / USD, plus the update time and loading feedback. These are actual account balances, not an estimate of remaining characters. Charges and free-credit deductions are determined by your DeepSeek account bill.

The local repeated-result cache distinguishes providers, language directions and context; the same original with different history does not directly reuse one result. DeepSeek requests share a fixed prompt prefix, allowing the provider's automatic prefix cache to work on a best-effort basis. Cache hits, fixed per-request costs and unmeasured percentage savings are not guaranteed. Optional history also counts as input; actual cost depends on input, output, context and cached usage.

For detailed setup instructions and troubleshooting, see the [Chinese getting-started guide](artifacts/风暴英雄助手新手指南.html). Download the HTML file to open it offline; the Windows package also includes it.

## Feature previews

Expand a feature to see its current controls. All screenshots use the same isolated offline demonstration; shown states are examples rather than evidence of live game or cloud success.

<details>
<summary>常用短句 — saved phrases and favorites</summary>

Use **新增短句** to add an expression, the star to favorite it, and **使用短句** to fill the reply area. Candidates in native `Ctrl+T` come from your own saved phrasebook. The favorites and reviewed statuses pictured here are sample data; the bundled seed phrases start unfavorited and unreviewed. Mark a translation as reviewed only after checking it; editing its text clears that status.

<img src="docs/images/v2.1/03-phrases.png" width="100%" alt="Current phrasebook page showing local phrase cards, favorites, and add, edit, and use controls">

*v2.1 current React components, isolated offline sample data; not live gameplay/cloud results.*

</details>

<details>
<summary>偏好设置 — readable text and window behavior</summary>

Under **外观**, **内容字号** changes the main content text and **主窗口保持置顶** applies to the visible desktop window. Preferences reuse one in-page management dialog. Main-window opacity and position locking remain available only in `-Legacy`.

<img src="docs/images/v2.1/04-preferences.png" width="100%" alt="Current preferences dialog on the Appearance tab with content font size and always-on-top controls">

*v2.1 current React components, isolated offline sample data; not live gameplay/cloud results.*

</details>

<details>
<summary>检查更新 — release notes and explicit update actions</summary>

Release notes use headings and lists that wrap to the available width. The v2.1 screenshot shows the state before a check. The v2.3 source checks once in the background on normal startup; click **检查更新** for a manual check, then choose the available download/install action. The sidebar dot represents a confirmed newer release and remains while installation is pending.

<img src="docs/images/v2.1/05-updates.png" width="100%" alt="Current update dialog before a check, showing the current version, a release-notes placeholder, and check, download, and restart controls">

*v2.1 current React components, isolated offline sample data; not live gameplay/cloud results.* The image shows the idle state before a check, not a real online check or download.

</details>

<details>
<summary>双击播报 — Tab callouts with their own status</summary>

**开启 Tab 双击播报** is off for a new user. After enabling it, open Tab in the game and double-click a supported item. This page shows the latest outcome and explains rejected attempts. Cooldown callouts require a verified positive allied heroic display; broader live validation is still pending as described below.

<img src="docs/images/v2.1/06-broadcast.png" width="100%" alt="Current double-click callout page with enable switch, usage instructions, and status feedback">

*v2.1 current React components, isolated offline sample data; not live gameplay/cloud results.* No message is sent to a game by this demonstration.

</details>

<details>
<summary>动作连按 — save a configuration, then start in the game</summary>

Choose `S` or alternating `Y+S`, set the interval, and click **保存配置**. Return to the game and use `F8` to start/stop with the default shortcut; saving alone does not start repetition. **取消连按** ends the current action. Open chat boxes can receive these literal key presses.

<img src="docs/images/v2.1/07-repeat.png" width="100%" alt="Current key-repetition page with mode and interval selection, 保存配置, 取消连按, and action status">

*v2.1 current React components, isolated offline sample data; not live gameplay/cloud results.* No game input is generated by this demonstration.

</details>

## Feature reference

<details>
<summary>Translation and optional helper capabilities</summary>

- **Incoming chat translation.** Receive team, party, whisper, and all-player chat, plus known-format draft-team chat (【征召团队】) during hero selection/bans. Lobby and display-history capture remain separate. English and Korean messages can be translated into Chinese. Source filtering and bounded rediscovery help avoid staying attached to an old match; live draft validation is described below.
- **Quick replies with `Ctrl+T`.** Enter Chinese, choose English or Korean, translate, and copy the result. Empty input shows a few favorite phrases; typing Chinese finds matching local candidates. Press Tab to enter the candidate list, use arrow keys there, then Tab or a mouse click to fill the text. Enter translates and copies; you paste/send it yourself in the game.
- **Your own keys and explicit provider switching.** Aliyun Machine Translation uses an AccessKey ID and Secret; DeepSeek V4.1 Flash uses an API Key. After verification and a translation test, the accounts are saved separately with encryption, and you explicitly choose the active service. DeepSeek disables thinking and optionally includes up to three anonymous originals from the same conversation; manual replies do not attach game history. Query actual DeepSeek balances or open Aliyun's character-quota console.
- **Subtitles and one Soft Lavender theme.** Choose the primary or another monitor for chat subtitles, save a position by dragging across screens, and recover to an available screen when the selected monitor disappears. Since v2.0, the helper uses Soft Lavender throughout the main window, settings and in-game overlays, with no theme selector. Existing theme preferences migrate automatically while preserving window positions, fonts and shortcuts. Transparent subtitles keep white text with a dark outline.
- **Reusable local phrases.** Add, edit and favorite expressions in the main window. A reviewed, exact Chinese match can reuse its existing translation when the current language has one unambiguous result. Missing translations, unreviewed entries and conflicting results still need translation. Editing the text clears its reviewed status.
- **Loot chest helper.** Prepare it in the helper, switch to the game's loot chest screen, and use Space to start, pause, or resume. Esc ends the session.
- **Configurable key repetition.** Choose `S` or alternating `Y+S`; each mode has its own saved interval of 20–5000 ms, with 20 ms as the default for new settings. `F8` starts/stops repetition; its shortcut can be changed to another F1–F12 key.
- **Automatic favorite talents — off by default.** Uses the game's existing favorite selection through its shortcut, then reads back whether that tier has been selected. It needs no translation key.
- **Tab double-click callouts — off by default.** Selected talent callouts begin with “注意” (“Attention”) and include verified quest progress in the same cell. Standalone baseline quests and verified positive numbers from the dedicated Tab cooldown label for allied heroics use their own icons; statistics callouts report only the clicked metric. Talent summaries and known quest names are bundled offline. No translation key is required.
- **Client updates.** Current source checks official stable releases once in the background on normal startup and shows the update dot when a newer release is confirmed; manual checks remain available. Choose when to download, verify, restart and update. The published v2.3 package includes startup checks and the shutdown handoff fix; older packages should be replaced manually. The updater supports `HotsHelper-<version>-Windows-x64.zip` assets; v2.3 and application version 2.3.0 represent the same version, and downgrades are not offered. Older local 1.1.0 packages require a manual download because their updater only recognizes Chinese asset names.
- **Emote handling and optional diagnostics.** Complete image-emote tags display as `[表情]`; emote-only messages skip cloud translation. Diagnostics are off by default and can be enabled for troubleshooting.

</details>

## Interface and navigation

<details>
<summary>Desktop architecture, behavior, and native overlay previews</summary>

**The v2.1 build uses the desktop interface shown above.** Keep the complete Windows ZIP contents; source changes do not update an older executable.

The six main pages, preferences, update checks, personal-key configuration and phrase editing use **React + TypeScript + CSS**, loading bundled local files through **pywebview + Microsoft Edge WebView2** without a development server or browser tab. Management actions share one in-page dialog; repeated clicks reuse it. Python handles capture, translation, persistence and game input. In-game subtitles and the `Ctrl+T` input bar retain native Tk windows, and `-Legacy` keeps the original Tk interface. These new interactions require a build containing the changes.

The main window follows the selected light-purple prototype, with a lavender sidebar, white content cards, and purple primary actions. Icon navigation opens chat, phrases, loot assistance, key repetition, talent assistance, and double-click callouts. Settings and update checks sit at the bottom of the sidebar. Soft Lavender is the only theme; older palette settings normalize automatically. The EXE, taskbar and window titles share the purple hexagonal swirl mark.

Under **偏好设置 → 外观** (Preferences → Appearance), content font size applies to React message/input text, and always-on-top applies to the visible WebView window. Standard and compact layouts are available. Main-window opacity and position locking currently remain available only in `-Legacy`. Opening preferences does not open quick input; disabling its hotkey leaves the quick-input button available.

**Move subtitles to the primary screen:** open **偏好设置 → 游戏内** (Preferences → In-game), select **主显示器** under **字幕显示器**, then click **移动到所选屏幕**. This moves subtitles to the selected screen's bottom preset and saves the choice. Other screens are selectable too; **保存并调整位置** and **保存并放到底部** also use the selected monitor. Editing on the same screen retains the custom position. The bottom preset estimates placement from that screen's work area and does not detect the game's HUD. In adjustment mode, drag across screens and release to save the position and monitor, then click **完成调整**. If a monitor is unplugged or its layout changes, subtitles recover to the nearest available work area; select a screen again if needed. This setting controls chat subtitles; the `Ctrl+T` input bar retains its independent placement. Synthetic-monitor and isolated-window checks cover the behavior; physical dual monitors and mixed DPI still require live validation.

**Draft chat and channel filters:** 【征召团队】 is allied chat during hero selection/bans. **全部聊天** (all chat), **团队与所有玩家** (team and all players), and **仅团队** (team only) include both regular 【团队】 and 【征召团队】. **仅征召团队** includes draft-team chat only. 【队伍】 (party) and 【密语】 (whisper) stay separate and are excluded from team-only filtering. The message list follows the selected filter, which also controls subsequent reception. Switching back does not recover messages filtered out in the meantime or delete existing messages.

Chat rows separate the nickname, channel, time, message, and translation. Circular avatars use local decorative illustrations; they do not identify the player's actual hero. Chinese input and translation preview appear side by side, with English/Korean pill choices for the target language. Translate, copy the result, and paste/send it yourself in the game. The separate `Ctrl+T` input remains available.

Manual translation errors stay beside the reply area and retain the draft for correction or retry. Copy uses the backend's current valid result. Helper pages distinguish waiting, running and stopped states and retain the latest outcome. Release notes have headings, paragraphs and lists that wrap to the available width. Normal startup in v2.3 checks once in the background; opening update management does not trigger another check, and manual checks remain available. Its sidebar dot reflects a confirmed newer release and remains while installation is pending.

Small windows automatically reduce spacing and supporting text and rearrange navigation and actions while retaining input, preview, and language choices. In-game subtitles and the quick-input bar remain separate.

The new desktop UI requires [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/). If it is missing, follow the startup message to install the official runtime; the app does not silently switch to an older browser backend. Complete built packages do not require Node.js. Source users can add `-Legacy` to the launch script for the original Tk UI. Memory and CPU must be measured again across the full process tree; old Tk measurements do not establish WebView2 performance or a sub-100-MiB memory footprint.

### Overlay option B adopted; A/B preview retained

**Option B · 原样换配色 remains the base.** Soft Lavender overlays use colors matching the main window, keeping the existing subtitle layout, single-line input, language dropdown and basic shortcuts. Transparent subtitles keep white text with a dark outline. Quick input uses the purple hexagonal swirl mark; Enter translates/copies, Esc closes it, and Ctrl+Tab changes language. A small local phrase list is added: Tab first enters the list, arrow keys select there, and another Tab or a mouse click fills the Chinese text before Enter translates/copies it. Candidate shortcuts do not take over IME composition. Candidates come from the user's phrasebook; unreviewed entries do not promise offline translation. Release builds check WebView2 page loading and offline bridging; in-game display, focus, input and cross-DPI behavior still require machine-specific validation.

You can also double-click `打开悬浮窗对比.cmd` in the repository root: it opens option B's quick-input preview without PowerShell or a persistent command window. Alternatively, run `预览悬浮窗AB.ps1` to open the comparison console. Switch “A · 原型新样式” / “B · 原样换配色” and “默认字幕” / “消息面板” / “快捷输入”; use “切换浅色背景”, “下一条消息” and “恢复位置” to compare backgrounds, advance messages and reset positions. **A**, with its two-column input, rounded corners and pale purple/white panels, remains an alternative preview. Switching previews does not change production preferences.

```powershell
.\预览悬浮窗AB.ps1
.\预览悬浮窗AB.ps1 -方案 B -状态 input
```

`-方案` accepts `A` or `B`; `-状态` accepts `subtitle`, `panel` or `input`, defaulting to `B` / `input`. Both previews use Tk and the existing project virtual environment, without npm, builds or network access. They do not read the game, credentials or preferences, call cloud services, or register a global `Ctrl+T`; the shortcut works only inside the preview. Select a preset Chinese phrase from the dropdown and click “填入输入条” to try its offline English/Korean results; other input prompts you to select a demo phrase. Copy and translate/copy actions replace the clipboard contents. The preview compares appearance and interaction; it does not establish in-game display, focus or input behavior.

</details>

## Using the optional game helpers

On the **双击播报** page, choose **中文 / English** under **发给队友的播报语言**. The choice is saved for future launches and defaults to Chinese. English uses bundled offline names/effects for heroes, talents, base quests, heroics and stat labels, for example `[Ally] Player (Anduin) | Hero damage: 12,345`. Latin-script player names are retained. Other names, including localized computer labels, use `Player N` from the verified player slot alongside the English hero name; names are not guessed or machine-translated. No cloud translation is called. Changing language cancels unsent actions; the next double-click uses the selected language. Missing English records are rejected rather than replaced with Chinese text or internal IDs. This setting is independent of chat translation and does not establish live support for an English game client.

<details>
<summary>Detailed operating steps, safeguards, and live-validation limits</summary>

**Key repetition:** choose a mode and interval, click **保存配置** ("Save configuration"), return to the game, and press `F8`. Saving settings ends the current action rather than starting a new one. Esc, Enter, or leaving the game ends repetition; it does not automatically resume when you return. These are literal key presses, so an open chat box can receive `S` or `Y`. Actual timing depends on Windows scheduling and the game's response.

**Favorite talents:** first mark your preferred talents in the game and keep its quick-favorite shortcut set to `Ctrl` + grave accent (the key below Esc). Enable **天赋辅助** in the helper. It reads the current hero's actual talent tiers, including special unlock levels, and defers while another helper owns input. Both the feature and its saved preference start disabled for a new user.

**Brightwing fix in v2.2:** the valid level-20 talent ID `BrightwingArcaneFlareHush!` was rejected by the old identifier validator. Because the reader loads every tier, this also blocked automatic selection at low levels. The validator now accepts the `!` and apostrophe punctuation used by the bundled catalog, also covering equivalent Raynor/Zul'jin IDs. Bounds, ASCII, identity, build and foreground guards remain. Tests use all 21 bundled Brightwing talents across seven tiers with synthetic memory and simulated input; live verification on affected computers is still pending.

**Double-click callouts:** enable **双击播报**, return to the game, and open Tab. Double-click one selected talent or statistics cell, then release the mouse and other keys while keeping the game foreground. Keep the game's `Ctrl+Enter` team-chat shortcut at its default. The helper checks the target, team channel, and empty chat box, then reads the entered text back before submitting. It does not change the clipboard or replace a user draft.

Use Tab's **talent page** for talents, baseline quests and heroics. Talent messages begin with “注意” and retain the hero's actual unlock level, name and short effect. A verified quest in that same selected cell adds its current count, including a real zero, or reports completion. Both allied and enemy quests are supported when their native bindings and displayed state are trustworthy. Standalone baseline quest names resolve by exact hero/quest ID: the offline directory contains 16 entries with explicit Face and localization evidence. A missing name becomes “基础任务1/2” only after the native identity and value checks still pass. Hidden, untracked or unknown counters are not treated as zero, and stale hidden labels are not used.

Double-click an **allied heroic icon** to report a visible, verified positive number from its dedicated Tab cooldown label, using “冷却显示：X秒” (“Cooldown display: X seconds”). Enemy heroic cooldowns are not read. The engine chooses this display from two timing channels, and it may show charge recovery. It does not establish that the heroic is currently unusable, has no charges left, or will become castable after that time; charges, resets and variants can affect availability. Zero, blank or hidden labels never imply “ready”, and remaining charges are not guessed. Tracer’s dedicated charge UI remains unverified and unsupported; unresolved abilities or changing bindings produce no callout. This uses the game's common UI structure rather than saved addresses from one machine. Build/layout/identity mismatches remain unknown. The 16 model decoders locate player models; quest and cooldown display checks are additional stages.

When a heroic talent icon and its cooldown layer represent the same verified player, ability and native position, the mapper emits one cooldown target. A verified positive display produces a cooldown callout; without one, no cooldown message is generated and ordinary talent behavior remains available. Overlapping unrelated targets are still rejected.

Earlier live read-only comparisons include Kael'thas Convection at `0 → 2`, an enemy Arthas baseline quest at `6 → 8`, and allied heroic displays of `38/29` seconds. Xal'atath's try mode additionally yielded `42/9` seconds and confirmed that the earlier talent/cooldown overlap caused clicks to be rejected. After the v2.1 fix, only Xal'atath's 45-second display has been confirmed to map to one heroic cooldown target; final chat submission still awaits user feedback. These are observed samples, not live acceptance of every hero, language, resolution or final chat submission. The name-directory count is not a count of heroes tested in game.

Chat boxes, menus, loot actions, repetition, quick input, and update preparation can block a callout. Unknown or untrusted data is not sent. Attempts are limited to once per item within 3 seconds and at least 2 seconds between items; toggling the feature does not reset those limits. A failed attempt is not automatically retried. If text remains in chat, inspect the reason on the helper's **双击播报** page and press Esc to clear the draft before trying again. If the status says the message may already have been sent, check chat history first.

The lobby/match label at the bottom of the main window describes the chat capture source, not the callout result. Callout status appears on its own page. Leaving a match invalidates an old pending callout even if Tab remained visible; open the panel and double-click again in the new match.

</details>

## Local data and privacy

User data lives in `%LOCALAPPDATA%\HotsChatHelper`. Existing preferences, phrasebook data, and encrypted credentials retain this location across upgrades.

- Each provider's keys are saved in a separate **Windows DPAPI** profile bound to the current Windows user; existing Aliyun DPAPI credentials remain compatible. Use your own keys; credentials and user databases are not part of the public distribution. The shared UI snapshot contains provider/configuration state, masked keys, balances and safe feedback, never the full AccessKey ID/Secret or DeepSeek API Key.
- Normal startup does not read the project's `.env` or use a shared key. Keys in `txt/` or other local test files are not automatically imported, and those files are not packaged or automatically uploaded. In the Aliyun form, **导入本机旧 .env** (“Import a local legacy .env”) reads only a file you explicitly select, up to 16 KiB. It fills the two fields without connecting automatically. Switching providers, submitting or closing clears entered credentials; saved keys are never prefilled.
- Old `usage.sqlite3` and `usage.sqlite3.initialized` files are left untouched and no longer read or created. A missing or corrupt old ledger does not block setup or translation.
- The current translation text is sent to the Aliyun or DeepSeek service you explicitly choose. With DeepSeek context enabled, up to three anonymous preceding originals from the same conversation are also included, without attaching sender nicknames; disabling it omits history. Manual replies send only the current input. Failures do not automatically switch providers. The visible chat history keeps the latest 500 messages for the current session and clears when the helper exits.
- **Diagnostics are off by default:** no diagnostic writer thread or recording file is created until enabled. The single switch records chat originals, translations and replay inputs/states for translation, automatic talents and double-click broadcasts. There is no separate detailed mode or ten-minute expiry. Logs default to a total cap of 100 MiB, rotating the oldest files and retaining up to seven days. Configured full keys/secrets are recursively redacted; review chat privacy before sharing an export. Startup failures have a separate bounded `startup.log`, described below.

In **偏好设置 → 诊断** (Preferences → Diagnostics), **导出诊断日志** runs in the background and displays the completed ZIP path for copying. Inspect the file and share it yourself; the helper does not upload it automatically.

### Startup troubleshooting

Windows can propagate a downloaded ZIP's origin marker to DLLs under `_internal`, preventing Python.NET or WebView2 assemblies from loading. Before importing the application, v2.2 frozen Windows builds remove this marker only from the three bundled managed DLLs used by Python.NET/WebView2; their binary contents remain intact. Source runs do not perform this preparation. Failed preparation or startup produces a native error message.

For a verified official archive, open the ZIP's Properties, select **Unblock**, and extract the complete archive again. For an already extracted application, unblock the complete application directory; unblocking just the EXE is insufficient. In PowerShell, replace the sample path with your own extracted application directory:

```powershell
Get-ChildItem -LiteralPath 'D:\your-extracted-folder\风暴英雄助手' -Recurse -File | Unblock-File
```

Startup failures write `%LOCALAPPDATA%\HotsChatHelper\startup.log`, keeping only the latest exception chain with a 128-KiB limit. Successful startup does not create it. Native error reporting is still attempted if writing the log fails. This log is separate from optional diagnostic recording and does not actively read credentials, environment snapshots or frame-local variables; inspect exception text and paths before sharing it. A genuinely missing WebView2 Runtime must still be installed from the [official Microsoft page](https://developer.microsoft.com/microsoft-edge/webview2/).

<details>
<summary>Troubleshooting recordings, replay fields, and decoder validation</summary>

**Lobby messages work, but match capture says “等待对局聊天，自动恢复” (“Waiting for match chat; recovering automatically”).** Lobby and match capture are independent. This status means match chat history has not been connected; it does not establish that the game process was not detected. During an active match, select the team channel and send `hello`. Confirm that it appears in the game's chat, then check whether the helper receives the original text. If the original is missing, investigate capture first; if only the translation is missing, check automatic translation, language support and provider configuration. Supported display-history structures use the Simplified Chinese labels “团队”, “征召团队”, and “所有玩家”; other client languages and unverified record structures require separate validation.

The source fix rechecks changed snapshots when a history containing only lobby-channel messages gains a team message during the same scan, rather than skipping that root for the rest of the scan. The cursor cache remains bounded. This does not resolve every waiting state: a new message in an already-scanned area can still require another scan if no later candidate refers to that history. A new build containing the fix is required; existing executables do not change when source files are updated.

**Chat without a displayed time was missed by the old capture entry.** That entry required a timestamp marker. v2.4 also recognizes the validated Simplified Chinese team/draft-team/all-player display-history structures without that marker, preserving node, history-link and channel checks. Players can keep their preferred in-game time-display setting. A message without a native time retains an empty time field; local wall-clock time is not substituted. Synthetic tests cover these structures, draft-to-match transitions and the offline translation flow; the affected player's actual format, live draft capture and cloud translation still require a new-build check. Unverified native record types are not read using guessed layouts. To test draft chat, send a marker in 【征召团队】 during selection/bans, confirm it is visible in the game, note the time and phase, and export diagnostics.

With diagnostics enabled, match-capture summaries use `capture_schema=2` for the timestamp-optional capability; merge and queue stages can retain their own schemas. `capture.state`, `source=match`, `stage=initial/discovery/inventory` cover initial discovery, recovery and background inventory. Every scan has a `scan_id`, with start/progress and candidate-found, exhausted, timeout, failed or cancelled outcomes as applicable. Progress is limited to once every five seconds; terminal outcomes are immediate. `candidate_found` is distinct from reader attachment, which gets a `reader_id`. Bounded polling summaries continue without new messages; enabling diagnostics mid-run records current observations rather than inventing earlier events.

Summaries include requested/read bytes, short reads, structural rejection and timestamp/channel/message-format checks. Fixed reasons do not copy rejected message bodies or senders. `timestamped_message_count`/`untimestamped_message_count` count successful full parses with/without a timestamp. These and other check counts include repeated polls; actual delivery uses `message_count` and `publish`. Scan `read_calls/read_bytes` cover scan blocks only; polling values cover that reader's cumulative reads, with `scan_id/reader_id` defining the boundary. `source_message_id` links a child source's message ID to its merged ID, queue and existing translation events. Synchronous handoffs can write these lines in a different order; correlate by ID. No prefix candidates only establishes a missing match in the scanned range, not that the game had no messages or a game setting was wrong.

To collect a useful report from an affected player:

1. Use a new build containing these logs and enable diagnostics before entering the match.
2. Keep the original client language and match mode. In an active match's team channel, send `HOTS_CAPTURE_TEST hello`, confirm it appears in the game's chat, and note the local time.
3. Keep the helper running for at least 90 seconds. If still waiting, reconnect once and retain about 30 more seconds of recovery logs. These durations expose logging stages; they do not guarantee connection within a fixed time.
4. Export the diagnostic ZIP before closing the helper. Include client language, match mode, marker time and whether the helper displayed the original. Unified logs include chat originals/translations; review them before sharing and do not attach credentials.

Repository `AGENTS.md` and the [hots-diagnostics skill](.agents/skills/hots-diagnostics/SKILL.md) require new features, behavior changes and fixes to include correlated diagnostics and real ZIP-export validation. Documentation-only and presentation-asset changes without behavioral effects do not need artificial business events; configuration or data that changes behavior still requires validation. This development/review requirement does not replace live acceptance.

For automatic talents and Tab double-click broadcasts failing together, use a new build containing replay recording, keep the original client language/resolution/scaling, and enable diagnostics before entering a normal match. Enable both helpers, note when an automatic selection fails, double-click one selected talent and one statistic in Tab, then export the ZIP. For translation failures, reproduce incoming chat or manual translation while recording. Include approximate times, game build, match/window mode, Windows scaling and monitor count. An existing EXE does not gain these events from a source update.

The new `helper.read` events distinguish `source=talent` and `source=broadcast`. They record actual/expected game build, connection/identity/talent/board/coordinate stages, fixed failure reasons, exception types and available Win32 error codes. `identity_reason` explains native identity rejection without exposing pointers or decoded state. Broadcast reads also record client size and desktop origin, render size, coordinate rejection reason, verified raw talent/stat cell counts, rejected metadata/coordinates, final hit count, read calls/bytes and elapsed time. Fields appear only when observed in that sample; missing render dimensions after an early failure are unknown, not zero or cached values.

`helper.click` records each sampled left-button press, click position, unique/ambiguous/no hit, hit kind and queue age. `attempt_id` links that press to later validation within the broadcast helper; it is not a double-click gesture ID. Chat/menu/foreground states retain `true`, `false` or `unknown`. Changed fixed cancellation reasons produce new status events. Replay records preserve operation order and state; diagnostic failures must not stop game helpers. Summary fields omit raw memory addresses; decoder replay includes encoded words, key/salt values and the computed pointer, rather than a full memory dump. Credential files are excluded, and exports include chat/replay content.

`board_profile=98348-quest-cd-1` identifies the quest/cooldown field profile. `board_code_verified=1` means code checks passed for six native getters: talent quest state/value, baseline quest index/state/value, and HeroicAbilLink. A zero means verification has not passed; changed code leaves the result unknown instead of trusting the same field offsets.

Quest events include `quest_raw_state/quest_raw_progress`, the interpreted `quest_state/quest_progress`, and `progress_reason`. Allied heroic events include `cooldown_state/cooldown_seconds/cooldown_reason`. `cooldown_alias_count` counts merged ordinary talent targets representing the same heroic; raw read counts remain unchanged. The same optional 100-MiB recording includes these observations, chat originals and translations. Unknown or unobserved values remain unknown.

Both features share the supported-build check (currently 98348), process/module access, native local-player identity and hero/talent catalog reads. Matching failures at `connect` or `identity` indicate a possible shared cause. Automatic talents do not use Tab pixel coordinates, so a 2K coordinate conversion problem alone does not explain both failures. English-client compatibility still needs live validation; added diagnostics locate failures rather than claim to fix them.

Offline replay: run `python -B -m tools.replay_diagnostics <diagnostic.zip>` from the development environment. The default JSON report is written beside the ZIP; use `--component translation/talent/board` or `--session <id>` to filter. The tool streams known JSON logs without extracting/running archive code, calling cloud APIs, opening a game or sending input. `replay.frame` entries feed recorded boundary inputs and state to actual business code and compare results. Recorded cloud/Windows/game responses are simulated inputs, not a reproduction of those external systems. Missing frames, rotation, truncation, redaction and write failures are reported as incomplete; legacy logs without frames cannot be replayed. A matching replay does not mean the recorded failure is fixed.

Use `--component reader` for decoder guards, pointer arithmetic, and quest/cooldown display decisions. Recorded `quest_progress` and `cooldown_display` frames replay their observed values, display state and required checks through the same parsing functions, without reconstructing the entire live UI.

Build 98348 supports model-decoder branches 0–15, with a compute-code fingerprint check for every branch. Branches 2, 5, 8, 11 and 14 require a valid runtime salt; missing values are never replaced with zero. A `decode_pointer` frame records encoded words, two ordered key reads, the salt used when required, and the computed pointer. Its `computed` outcome validates arithmetic only; later model validation can still reject a changed selector, root, slot, key or salt pointer/value. All 16 algorithms passed synthetic reads and 303,810 independent native-instruction comparisons. These are offline results, not live acceptance of every branch on affected players’ machines; English/2K and match-specific behavior still need live validation. A selector is not a fixed user property, and current observations do not establish whether it changes on each match or restart. Historical expected rejections may differ after new codec support; old logs without encoded words, keys or required salt cannot reconstruct those computations.
- Diagnostic exports contain logs and a safe summary, exclude credential files, databases, and preferences, and are not automatically uploaded. Exporting does not call the cloud translation service.

</details>

## Run and develop from source

<details>
<summary>Source setup, tests, packaging, and output cleanup</summary>

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
.\.venv\Scripts\python.exe -m tools.build_release --output "releases/v2.4"
```

The build first runs `npm ci` and `npm run build`, checks the frontend output, then freezes Python with PyInstaller. It includes `frontend/dist`, the Windows WebView2 bridge, and pythonnet dynamic libraries; it does not bundle Node.js, a development server, or Qt/CEF browser backends. The target computer needs WebView2 Runtime.

The command above writes `releases/v2.4/HotsHelper-2.4.0-Windows-x64.zip`. The ZIP’s directory and `风暴英雄助手.exe` retain their Chinese names. The internal version is `2.4.0` and the channel is `stable`. Running the local build command alone does not create a Git tag or public Release; this document does not link an unpublished v2.4 asset. The build explicitly stages public seed data and bundled UI files and rejects local credentials, databases, preferences, logs and sampling files from the release bundle.

Packaging runs `--check-installation` for native operations and seed data, `--check-desktop-installation` for desktop resources and dependencies, and `--check-desktop-rendering` to load the local WebView2 page and verify the React demo state and JavaScript bridge. All three must pass before creating the ZIP; the manifest records each result. The bundled “查看界面效果.cmd” starts an offline preview directly. These checks confirm loading and bridging; visual similarity, DPI behavior and in-game behavior still require separate validation.

Old packages are cleaned up only after the new package passes its installation check, every ZIP entry matches the manifest's size and SHA256, and `发行校验.json` is written successfully. For `releases/` and its release subdirectories, the current ZIP, manifest, and build directory are retained; only recognized older packages and build directories are removed. Unrelated files and source directories are preserved. A failed build or verification keeps old packages. An external `--output` only cleans recognized products inside that directory, never its parent; directories containing symbolic links or junctions are skipped.

</details>

## Validation scope

The v2.3 fix passed **179 related Python tests, 33 frontend tests and a frontend build**, covering update transactions, host PID propagation, native snapshot availability, one-time host shutdown and IPC lock ordering. The **2.3.0 release build** passed installation, desktop dependency and actual WebView2 rendering/bridge checks; 99 frozen application modules match the `v2.3` source tag. The public ZIP passed updater detection, download, SHA256 verification and isolated extraction; 2.2.0 finds the update and 2.3.0 is not offered the same version. Real cross-version in-place replacement remains unverified. See the [v2.3 Release](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v2.3) for assets and checksums.

The v2.2 release source `38df948` passed **375 Python tests across 24 isolated test modules and 33 frontend tests**, plus Python syntax and dependency checks. The frozen package passed installation, desktop dependency and actual WebView2 rendering/bridge checks. After adding download markers to all three managed DLLs, startup removed them and rendering passed again. The real published asset passed the 2.1 updater's check, download, SHA256 verification and isolated extraction; no in-place installation was performed. The affected computers/live game still need verification. See the [v2.2 Release](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v2.2) for assets and checksums.

<details>
<summary>v2.1 regression results and scenarios still requiring manual validation</summary>

The final v2.1 regression run passed **570 Python tests and 33 frontend tests**. For validation scope, artifact checks, build commit and SHA256, see the [v2.1 Release record](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v2.1). The earlier unified source at commit `51daf8e` (merged as `69ec5a3`) passed **1,247 tests across 113 isolated test modules, plus 20 acquisition probes**. These figures describe the earlier verification baseline, not the new v1.2 test pass. See the [v1.2 release](https://github.com/renyouhao/heroes-storm-helper/releases/tag/v1.2) for its historical test counts, artifact checks, source commit, and SHA256. Source-test results alone do not establish that every in-game scenario or update path has been exercised.

Local checks target Chinese client build 98348. Talent and statistics callouts have been confirmed in team chat in an earlier fix package, and favorite-talent checks include selected heroes and special talent tiers. The following still need broader manual validation:

- Natural talent progression across successive normal and Brawl matches, including uninterrupted multi-match runs.
- The English game client, other client builds, and broader resolution/window-mode combinations.
- End-to-end input timing and game acceptance of repeated actions on different machines.
- Cross-version replacement between real frozen application packages. The update transaction has temporary-directory tests for replacement and rollback; startup checking only confirms short-term process survival.

Talent readback confirms that a tier became selected; it does not independently verify the current favorite target. Polling intervals are scheduling targets, not same-frame guarantees. Unknown game builds, identities, or modes do not authorize automatic talent selection or callouts. Spectator and replay use have not been validated.

For fuller behavior notes, troubleshooting steps, and historical validation details, use the [Chinese README](README.md) and [Chinese HTML guide](artifacts/风暴英雄助手新手指南.html).

</details>

## Version notes and compatibility

<details>
<summary>v2.1 changes, earlier releases, and supported model decoders</summary>

**New in v2.2:** startup preparation for the bundled Python.NET/WebView2 DLL download markers, native startup-error messages and bounded exception-chain logs; corrected talent identifiers for Brightwing and equivalent Raynor/Zul'jin entries.

**Startup checks in v2.3:** normal startup checks official stable releases once in the background and lights the existing update dot when a newer version is found. Capture reconnection does not repeat the check; demo and installation checks remain offline. A known update keeps its dot through transient network failures or cancellation; a successful check confirming no upgrade clears it. Download and installation remain explicit user actions.

**Update handoff in v2.3:** the installer waits for the desktop host PID. The native runtime keeps snapshots available until the host observes installer readiness, closes its window, shuts down the native child and releases the single-instance mutex. Replacement starts after the host exits. Preparation reports archive/file validation, runtime copying and installer startup stages. Real frozen-package cross-version replacement still needs live validation.

**New in v2.1:** one React management dialog for preferences, accounts, updates and phrase editing; applied content font size and always-on-top; accurate status/update dots and structured Markdown release notes; local phrase candidates in native `Ctrl+T`; valid-result copying and draft recovery after failure; and merging the same heroic talent/CD overlay into one hit target.

**Introduced in v2.0:** the React + WebView2 desktop, a single Soft Lavender theme and purple hexagonal swirl icon, the existing option-B overlay layout and controls, unified 100-MiB diagnostic recording/replay, and all model-decoder branches for the supported game build.

Build 98348 supports **all 16 model-decoder branches, 0–15**, with a verified compute-code fingerprint for each. Branches 2, 5, 8, 11 and 14 additionally require a runtime salt. Reads recheck the selector, table root, encoded slot, used keys, and salt pointer/value before trusting the result. All algorithms passed synthetic reads and independent native-instruction comparisons; this does not mean all 16 branches were exercised on affected players’ machines or that newer game builds are supported.

Version note: `v1.1` remains a source-preparation tag; its Release was never published. During release preparation, GitHub stripped Chinese characters from the asset name. The tag is preserved, and the asset naming contract is corrected in `v1.2`, using an ASCII ZIP name shared by the publisher and updater. **Existing local 1.1.0 packages recognize only Chinese ZIP names, so download v2.3 manually using the link above.** Your local user data stays in place.

</details>
