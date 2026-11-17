# Compact transaction screening (#139)

Before captures use the screen from `dev` at `4fb57e7`, after the compact shell
was merged. After captures use the same synthetic transactions, accounts and
suggestions. Desktop: 1440 × 900. Mobile: 390 × 844. After captures include a
focused category selector and its full selected name.

| Theme / viewport  | Before                                 | After                                |
| ----------------- | -------------------------------------- | ------------------------------------ |
| Wireframe desktop | [Before](wireframe-desktop-before.png) | [After](wireframe-desktop-after.png) |
| Wireframe mobile  | [Before](wireframe-mobile-before.png)  | [After](wireframe-mobile-after.png)  |
| Black desktop     | [Before](black-desktop-before.png)     | [After](black-desktop-after.png)     |
| Black mobile      | [Before](black-mobile-before.png)      | [After](black-mobile-after.png)      |

The transaction description, amount, date and category action replace the large
explanatory banner at the top of the work view. Full account details and notes
remain available through disclosures. Position in the current queue is separate
from remaining/completed work.

`tests/screening.spec.ts` checks the save/advance flow, failed writes and retries,
terminal and nonterminal tags, queue counters, month navigation, loading/error/
empty states, suggestion failures, keyboard focus and long mobile content.
The fixture is in `tests/screening-fixture.ts`; no real financial data is used.
CI uploads additional loading and empty-state captures with `frontend-browser-checks`.
