# Compact application shell (#138)

Captured with synthetic operator data using `tests/shell.spec.ts`. Desktop views
are 1440 × 900; mobile views are 390 × 844. Navigation captures show keyboard
focus on the active Users route. Individual screen styling is outside #138.

| Theme     | Desktop                          | Mobile page                  | Mobile navigation                             |
| --------- | -------------------------------- | ---------------------------- | --------------------------------------------- |
| Wireframe | [Desktop](wireframe-desktop.png) | [Page](wireframe-mobile.png) | [Navigation](wireframe-mobile-navigation.png) |
| Black     | [Desktop](black-desktop.png)     | [Page](black-mobile.png)     | [Navigation](black-mobile-navigation.png)     |

The browser suite also captures navigation at 400 px height and asserts that
its final entry and version footer remain in view. All generated captures are
included in CI's `frontend-browser-checks` artifact.
