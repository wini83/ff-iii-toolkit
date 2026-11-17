# Toolkit visual foundation

The UI keeps Tailwind CSS and DaisyUI. Light mode uses `wireframe`; dark mode
uses `black`. The overrides in `src/routes/layout.css` preserve their neutral
character, introduce a restrained amber action accent and use explicit semantic
status colors. Dark surfaces are graphite rather than pure black.

## Conventions

- Use `bg-base-*`, `text-base-content`, and `border-base-300` for surfaces and
  separators. Avoid hardcoded white backgrounds and black text.
- Use `btn-primary` for the main action; neutral/ghost variants for secondary
  actions. Colors for success, warning and error communicate state, accompanied
  by text or an icon.
- Use DaisyUI component radii: 2 px for fields/selectors and 4 px for panels.
  Avoid large utility radii, decorative gradients, pastel icon tiles and shadows.
- Keep normal text proportional. Apply `toolkit-data` to amounts, dates and IDs
  (monospace, tabular numerals); `toolkit-icon` is a neutral icon wrapper.
- Buttons and form controls use a compact shared size. Tables use 8 px vertical
  cell padding. Keep touch targets adequate, using larger controls where needed.
- Retain visible keyboard focus and standard disabled/loading states. Wrap long
  content, and use a scrolling wrapper around wide tables rather than the page.
- New dialogs, dropdowns and cards receive thin borders and no component shadow.
  Defaults live in `utilities.toolkit`, after DaisyUI’s sublayer. Explicit Tailwind
  utility classes take precedence: remove old `shadow-*` and
  `rounded-*` decoration as each screen is migrated, rather than overriding every
  descendant globally. Do not change chart geometry or semantic colors globally.

## Theme preference

`src/app.html` resolves the preference before paint, including public routes.
The existing `ff-toolkit-theme` storage key is retained: `light` migrates to
`wireframe`, `dark` to `black`. Unknown/missing values follow the system scheme
at initialization. An explicit choice persists across reloads and navigation;
unavailable browser storage does not prevent rendering or switching themes.
Without JavaScript the document defaults to wireframe.

## Verification

Run `npm run check`, `npm run lint`, `npm run build`, then
`npm run test:matching` after installing Playwright Chromium. Theme tests cover
legacy/current preferences, switching, reloads, public routes and blocked storage.
They assert computed table padding, modal shadows and explicit utility precedence,
then capture synthetic user tables, status badges and focused/error dialog
states in both themes at 1440 px and 390 px. Frontend CI uploads `test-results`
as `frontend-browser-checks`. No production data or secrets are used.

This foundation does not redesign screen layouts. Compact navigation (#138),
screening (#139), the landing page (#140), statistics (#141) and remaining
workflow-specific decoration (#142) are separate stages.
