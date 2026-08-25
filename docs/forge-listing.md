# Forge listing copy

The text published on the mod's sp-mod.com page, kept here so it is versioned
alongside the code it describes and survives moving between machines.

Update the description whenever a release changes what the app does. The
"Update to X" line in the notice tracks the current version, same as the one in
`README.md`.

---

# 1. Mod description page

```markdown
## SPTChecker

A lightweight Windows desktop app that watches the [SPT Forge](https://sp-mod.com/mods) for new and updated mods — and, optionally, checks your own installed mods against it.

> **The Forge moved to sp-mod.com.** The Forge changed hands and now lives at **sp-mod.com**; the old `forge.sp-tarkov.com` is fully offline. **Update to 3.4.1** — anything before 3.3.0 points at the dead domain and cannot reach anything at all. Your saved history carries over automatically on first launch: mod ids were preserved across the move, so already-seen mods aren't re-reported and your tracked count stays intact.

> **Note on antivirus flags:** A small number of vendors may flag this exe as malicious. Most of them (ALYac, Arcabit, Emsisoft, eScan, GData, VIPRE) all run BitDefender's engine under the hood and are triggering off the same single false positive detection. These are not real threats. Source code is open on GitHub.

---

### What it does

SPTChecker runs quietly in your system tray and checks the Forge every 15 minutes for changes. When it finds something, it sends a Windows toast notification and updates the UI — no need to manually browse the Forge to stay up to date.

It can also do the reverse: point it at your SPT install folder and it'll scan your actually-installed mods and tell you which ones have updates waiting (see **Local mod scanning** below) — entirely opt-in, off by default.

---

### How it works

1. On first launch, the app populates both columns — **New Mods** and **Recently Updated**, mirroring the website's own tabs.
2. Both columns are built from the Forge's RSS feeds and public API — **no HTML scraping**.
3. Every 15 minutes it re-fetches and compares against stored state — matching the shortest cache window sp-mod.com serves, since checking more often can't surface anything newer.
4. Newly seen mods are flagged **New**. The Recently Updated column tracks fresh releases of existing mods.
5. If a mod author unpublishes their mod, it's automatically removed from the display.
6. Results persist across checks and restarts — new findings push older entries down in a rolling history (up to 7 per column).
7. If a check fails (site unreachable, network hiccup, rate limiting), the app doesn't crash — it shows the error in the status bar and quietly retries on the next cycle.

A full check completes in roughly two seconds. Requests are paced and automatically back off if the Forge rate-limits, so the app stays a good citizen even when a check and a local scan overlap.

---

### UI

- **Two-panel layout** — New mods on the left, recently updated mods on the right (up to 7 per column), each heading showing a live count of what's in it
- **Mod cards** — Each card shows the thumbnail, title, author, version, category and description on a rounded surface outlined in its category colour, with a bolder rail down the left edge so a full column reads as a list rather than a grid of boxes
- **Version diff** — Updated mods show the version change as a chip (e.g. `1.2.3 → 1.3.0`)
- **View Change Notes** — A small icon on updated mod cards opens a popup with that version's changelog, anchored to the card it belongs to; click the same icon again (or press Escape) to close it. Rendered as actual markdown (bold, italic, inline code, headers, bullet lists, and clickable links that open in your browser) rather than a plain text dump, plus a direct "Open on Forge" button
- **NEW badge** — Freshly detected mods are marked with a green NEW badge so you can spot changes at a glance
- **NEW AUTHOR badge** — Mods from accounts created in the last 60 days are flagged so you can spot new community members
- **Timestamps** — Each card shows how long ago the mod was published or updated (e.g. "2h ago", "yesterday"), pinned to the top right where a long title can't push it off the card
- **Readable at any width** — Long titles and descriptions are trimmed with an ellipsis rather than cut mid-word; hover a card to smoothly scroll them and read the full text
- **Click to open** — Click any card to open the mod page directly on the Forge
- **Right-click menu** — Right-click any card for quick options to view change notes, open on Forge, or copy the link
- **Category legend** — Hover the info icon in the header for a color key to every mod category
- **Stats window** — Click **Stats** for headline tiles (total tracked, added in the last 7 days, average per day), a 30-day chart of new mods published per day (hover any point for the exact date/count), and the top 5 authors/categories by activity in the last 30 days, each row drawing a proportional bar so the shape of the ranking reads at a glance. Rolls forward automatically, no manual reset. Author names are clickable and open their Forge profile
- **Right-sized on first launch** — The window opens sized to show every mod without scrolling, measured against your actual display scaling rather than assumed. After that it remembers and restores whatever size you chose; shrink it and both columns scroll
- **Dark theme** — Custom-drawn controls throughout, styled to match the SPT aesthetic

---

### Local mod scanning (opt-in)

Click **Local Mods** to check your own installed mods against the Forge — entirely optional and off by default. If you run into anything odd, please report it.

1. Point it at your SPT install folder (validated automatically — it checks for `BepInEx/plugins`, or the server mods folder under either `SPT_Runtime/` (SPT 4.1+) or `SPT/` (SPT 4.0)).
2. Click **Scan Now**. Rather than guessing a mod's identity from its filename or code shape, the app reads each mod's *real* declared metadata via actual .NET reflection, so it works no matter how a mod author structured their code. A progress bar tracks each mod as it's checked.
3. Each mod is matched against the Forge — exact ID lookup first, falling back to a smarter name search that handles camelCase/dotted internal names, author-prefixed names, and a full-text search, then conservative fuzzy ranking. Results lead with a summary line and colour-coded counts, then break down into sections:
   - **Updates Available** (orange) — full mod cards (thumbnail, version diff, changelog, right-click menu — same as the main feed), with an **Open All** button to launch every one of those mod pages at once
   - **Up to Date** (green) — a simple clickable list linking to each mod's Forge page
   - **Not Found on Forge** (grey) — mods that couldn't be confidently matched (see below for why)
   - **Couldn't Check** (red) — only appears if the Forge rate-limited the scan partway through. These mods are almost certainly fine; the app just didn't manage to ask about them, and says so rather than reporting them as missing
4. Every match is then confirmed against the Forge's own update service, using **your actual installed SPT version** (read directly from `SPT.Server.exe`). This catches things a plain version comparison can't:
   - A newer release that isn't compatible with your SPT build is **not** offered as an update
   - A newer release blocked by another mod's dependency requirements is **not** offered as an update
5. Mods that ship both a client-side plugin and a separate server-side file (common for larger mods) are recognized as one logical mod and shown as a single result, not two duplicate entries. If the two halves are at different versions, both are labelled (e.g. `client 1.4.0, server 1.5.0`).
6. A toast notification fires if any installed mods have updates available.
7. Your folder path and last scan results are remembered across restarts and shown again next time you open **Local Mods** — but a scan only ever runs when you click **Scan Now**. It never runs automatically, on startup or otherwise.

**Why a mod might show up as "Not Found on Forge":** its internal name may not closely resemble its actual Forge listing title, it may be part of a bundled mod pack rather than listed individually, it may have been removed or renamed on the Forge, or it may be a core SPT/framework component rather than an actual mod (filtered out on purpose). SPTChecker would rather report "not found" than risk flagging the wrong mod as needing an update.

**A note on version numbers:** the app reports what a mod's own files declare. If an author bumps their Forge listing but forgets to update the version compiled into the mod itself, it can still read as out of date after updating. That's a mismatch in the mod's own metadata, and it has to be fixed by its author.

---

### System tray

- Closing the window minimizes to tray instead of quitting
- Double-click the tray icon to restore the window
- Right-click for **Show**, **Check Now**, or **Quit**
- **Tray badge** — A red dot appears on the tray icon when there are unread changes while the app is minimized, with an unread count in the tooltip. Clears automatically when you open the window
- Toast notifications work even while minimized

---

### Status bar

- **Forge status indicator** — A dot shows whether the last check reached the Forge OK (green) or failed (red); hover for details
- **Errors you can't miss** — A failed check is called out in red rather than blending into normal status text
- **Update available** — A green chip appears when a newer SPTChecker has been posted to the Forge; click it to open the mod page. Invisible when you're up to date, and it only ever tells you — nothing installs itself
- **Live countdown** — Shows time remaining until the next automatic check
- **Running summary** — Shows the baseline mod count on first run, or how many new/updated mods were found (and total tracked) on each subsequent check

---

### Additional features

- **Run on Startup** — One-click toggle to launch silently in the background when Windows starts
- **Thumbnail caching** — Mod thumbnails are cached to disk and auto-purged after 3 days; mods with no uploaded thumbnail get the same wireframe placeholder icon shown on the Forge itself, instead of a blank box
- **Unpublished mod detection** — Mods removed from the Forge are automatically cleared from the display, verified in a single batched lookup rather than one request per mod
- **Fails safe, never guesses** — Ambiguous matches are reported as unmatched rather than guessed at, and any network failure leaves the display untouched instead of hiding mods
- **Low resource usage** — Connection pooling, shared fonts, a smart timer that sleeps when the window is hidden, and state writes skipped when nothing changed
- **Standalone .exe** — No Python installation required, just download and run

---

### Credits

Huge thanks to **AlexTushonka** for taking on the Forge, keeping it running at sp-mod.com, and being genuinely great to deal with.

Local mod scanning was inspired by Refringe's **Check Mods** CLI.
```

---

# 2. Patch notes — v3.4.1

```markdown
# v3.4.1 — Bug fixes

Two fixes for issues introduced by the 3.4.0 visual overhaul, both reported
from real use.

- **Closing a popup no longer opens another one.** Clicking the **X** on the
  Stats window could trigger the **Local Mods** button sitting underneath it,
  leaving you with a second window to dismiss before getting back to the app.
  The popup was closing on mouse-*down*, which handed the mouse-*up* to
  whatever was behind it.

- **Change notes no longer cut off their last lines.** A changelog longer than
  the popup but shorter than about 24 lines would simply stop partway, with no
  scrollbar and no response to the mouse wheel — the remaining lines were
  rendered below the visible area with no way to reach them. Scrolling now
  starts as soon as the content actually overflows.

Both fixes are on the shared components, so they apply to every popup and
every button in the app rather than just the two cases that surfaced them.
```

---

# 3. Patch notes — v3.4.0

Publish these alongside 3.4.1 if 3.4.0 never went public — otherwise anyone
updating from 3.3.3 gets no explanation of the redesign.

```markdown
# v3.4.0 — Complete Visual Overhaul

> ## **SPTChecker has been redesigned from the ground up.**
>
> **Every card. Every button. Every window.** Same features, same behaviour —
> it just finally looks the part.

### At a glance

- **Completely redesigned mod cards** — rounded, spacious, and readable
- **No more text chopped off mid-word**
- **Thumbnails that actually fill their tile**
- **A real header bar**, with proper toggles and buttons
- **A rebuilt Stats window** with headline numbers and ranking bars
- **Refreshed colours** across the entire app
- **Opens at the right size** — no more resizing it every time

---

## The cards

- **Redesigned from scratch.** Rounded corners, a proper surface, and real
  breathing room. The category colour is still there, but as a thin rounded
  border with a bolder rail down the left edge instead of a flat 2px box — so
  a full column now reads as **a list of mods**, not a wall of competing
  outlines.
- **No more text cut off mid-word.** Long titles and descriptions end in a
  clean ellipsis, then scroll to reveal the rest when you hover.
- **A clearer hierarchy.** Title, then version and author, then description —
  each on its own line, with the mod's age pinned to the top right where a
  long title can't shove it off the card.
- **Version chips.** Updates show `1.4.3 → 1.4.4` in amber at a glance, and
  the NEW / NEW AUTHOR badges are now **subtle tinted pills** instead of loud
  blocks fighting the title for attention.
- **Proper thumbnails.** Rounded, and cropped to fill the tile — Forge banner
  art used to end up as a thin strip stranded in a mostly empty square.

## The rest of the window

- **A real header bar**, with the app icon, name and version.
- **Run on Startup is now a proper toggle switch** — replacing the Windows
  checkbox that always drew itself light-grey and never matched the theme.
- **Rounded buttons throughout**, with **Check Now** as the obvious primary
  action.
- **Column headings now show counts**, so you can see at a glance how many
  mods are in each list.
- **A tidier status bar.** Failures are called out in red instead of blending
  into normal status text, and a newer release shows up as a clickable chip.
- **A refreshed palette** with noticeably better contrast on the smaller,
  dimmer text.

## Stats

- **Three headline tiles** up top: total tracked, added in the last 7 days,
  and average per day.
- **Top Authors and Top Categories now draw a bar behind each row**, so you
  can read the shape of the ranking without comparing every number.

## Local Mod Scan

- **Matching toggle switch** and a proper input field for your SPT folder.
- **Results lead with a summary** and colour-coded counts — how many can be
  updated, are current, aren't on the Forge, or couldn't be checked.

## Change notes

- **The notes icon is now a toggle** — click it again to close what it
  opened, and it no longer stacks a second window on top of the first.
- **The popup is anchored to its card**, so you can see which mod you're
  reading about.

---

## Fixes

- **Scrollbars actually appear.** If a column held more mods than fit on
  screen, there was previously no way to scroll to them.
- **Cards no longer overflow at 125% / 150% display scaling.** Card height was
  a fixed pixel value while the text inside scaled with Windows, pushing the
  description out through the bottom of the card. Everything is now measured
  from your real display settings.
- **The window opens at the right size**, showing every mod without scrolling,
  at any display scaling.
- **Scan Now** no longer looks clickable when no valid SPT folder is set.

## Note on first launch

Because the cards changed size, **your saved window dimensions are reset once**
so everything fits. Resize it however you like afterwards — that gets
remembered as normal.
```
