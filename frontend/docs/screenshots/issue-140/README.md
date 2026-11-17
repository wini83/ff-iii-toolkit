# Compact task overview (#140)

Before captures use `dev` at `1c54556`, after #139 was merged. Both versions use
the same synthetic Firefly connection. After captures include 12 transactions
ready for categorization, zero unprocessed BLIK transactions and seven
unprocessed Allegro transactions. These are distinct statistics, with no total.
Desktop: 1440 × 900. Mobile: 390 × 844.

| Theme / viewport  | Before                                 | After                                |
| ----------------- | -------------------------------------- | ------------------------------------ |
| Wireframe desktop | [Before](wireframe-desktop-before.png) | [After](wireframe-desktop-after.png) |
| Wireframe mobile  | [Before](wireframe-mobile-before.png)  | [After](wireframe-mobile-after.png)  |
| Black desktop     | [Before](black-desktop-before.png)     | [After](black-desktop-after.png)     |
| Black mobile      | [Before](black-mobile-before.png)      | [After](black-mobile-after.png)      |

The hero, repeated Workspace areas and Amazon placeholder are replaced with four
workflow rows. Connection state and stored-data freshness use existing status
endpoints; each count links to its source statistics and shows its own snapshot
time. Categorization opens the oldest pending month when one is available.

`tests/overview.spec.ts` checks workflow destinations, zero/missing counts,
loading and failed jobs/requests, polling and cleanup, connection recovery,
fresh/stale/missing snapshots, focus, mobile overflow and both themes.
Additional unavailable-state captures are included in CI's
`frontend-browser-checks` artifact. All data is synthetic.
