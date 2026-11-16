# Shader contribution model

How player apps feed missing shaders and pipelines back into every game's
shared list. Client (in-app), inbox (server), and merge (maintainer) are
three separate stages; identifiers only, nothing from the game itself.

## 1. Collection (in-app runtime)

While playing, the runtime writes every shader and pipeline the shipped list
does not serve into `shader-list-contribution.txt`
(`totk_shader_contrib.h`: `totk_rt_contrib_line`). Only non-default states are
recorded — defaults are never written (`contrib_wanted`; every build already
carries each shader's default variant). Render pipelines the warm-up lists
lack are collected too, including their border-emulation (`B`) and tile-read
(`T`) variants.

## 2. Opt-in (one question, once)

Before the game starts, on the first start where the player has not chosen
yet, Mac (`app/launcher.m`) and iOS (`main.m`) ask the same single question
with two answers — "Contribute shaders" / "Don't contribute"
(`totk_contrib_upload.h`: `totk_inbox_prompt`, `totk_inbox_answer`). History:
it used to wait for 100 new rows first, so players meeting few new shaders
were never asked; since 2026-11-10 it asks regardless. The choice persists
beside the file (`.upload` sidecar: `auto`/`off`); the Settings switch flips
it at any time, and a legacy "never remind me" answer is still honored.
`TOTK_CONTRIB_INBOX` empty disables the whole flow (nothing asked, nothing
sent).

## 3. Upload (background, idempotent)

With `auto`, a `QOS_CLASS_UTILITY` queue POSTs JSON to
`https://mac-native-games.com/v1/contrib` 20 s after start and every 30 min
(`TOTK_INBOX_FIRST`, `TOTK_INBOX_EVERY`; chunks of ≤2,000 rows, the inbox
takes 5,000). Rows the inbox accepts retire into the done file
(`totk_contrib_retire`) so each row is sent once; rejected rows stay for the
next attempt. Per-request payload: schema, game id (`GAME_INBOX_ID`: `totk`,
`botw`, `oot`, …), random per-install UUID, game version, app build
(`MNGSourceCommit`), platform, OS version, device *model* (`Mac16,1` — shared
by every unit of that model), optional leaderboard id (Mac nickname link),
and the rows: shader/pipeline identifiers only (~1.2 KB max each). No game
data, saves, or personal information. Files are claimed per game id before
start (`totk_inbox_claim`); another game's file is moved aside and never sent.

## 4. Inbox (server, separate project)

`MacNativeGamesWeb/shader-inbox` (Vercel function + database) keeps each row
once per game with the installs and versions that sent it. Since 2026-11-12
it is the *only* intake: GitHub issues with attached contribution files are
no longer read.

## 5. Merge (maintainer)

`collect_shaders.py --inbox <url> --seed … [--apply]` merges inbox rows into
each game's `assets/shader_list.tsv`. Routing is by content, never by sender:
a shader key is the SHA-256 of its control section plus code, checked against
each game's prepared seeds (plus its own list for seeds that miss played
shaders), with a 4-rule cascade and `COMPLETE` games rejecting outside rows;
rows that a seed's built-in variants already serve are dropped. Each game
keeps its own list. `--mark-inbox` (admin token `SHADER_INBOX_TOKEN`) marks
merged rows done after the lists commit.

## Effect

Coverage converges: 18,244 variants shipped; of one day's 158 issue reports,
83% of shader rows and 99% of pipeline rows were already in the list. What one
player meets, every player's next build carries — nobody stutters there again.
