# Remaining workflow refresh (#142)

The work is based on `dev` after #148. Backend calls, route destinations,
matching decisions, polling, validation and confirmations retain their handlers.

- `WorkflowHeader` replaces decorative hero/quick-action cards with a title,
  description and the existing actions in a wrapping toolbar.
- Route-local classes replace gradients, large radii, colored icon tiles and
  shadows. No new global CSS override changes control behavior.
- Login and invite activation use one compact form panel. Login health/version
  remains visible on mobile as well as desktop.
- BLIK steps remain visible at mobile widths; file-ID entry and preview controls
  wrap within the viewport. Wide data tables retain their own scroll containers.
- Semantic warning/error colors, masked credential inputs, loading states,
  upload feedback and confirmation dialogs are retained.

## Route checklist

`workflows.spec.ts` checks the following remaining screens in both `wireframe`
and `black`, at 1440 px and 390 px. It captures synthetic screenshots, checks
page errors and horizontal overflow, and rejects old gradient/heavy-shadow
classes. Fixtures contain only synthetic data and credential metadata; no real
PDFs, CSVs, secret values or accounts are used.

| Route                         | Covered state                                               |
| ----------------------------- | ----------------------------------------------------------- |
| `/login`                      | Form, version/health                                        |
| `/set-password`               | Missing invite; form with synthetic invite                  |
| `/blik/upload`                | File picker, selected file, failed upload                   |
| `/blik/file`                  | File-ID entry, visible workflow steps                       |
| `/blik/file/synthetic`        | Preview loaded through client navigation                    |
| `/blik/file/synthetic/match`  | Populated candidates and original actions                   |
| `/allegro/accounts`           | Populated list; empty, locked and error states              |
| `/allegro/synthetic/payments` | Populated payments and pagination controls                  |
| `/allegro/synthetic/matches`  | Populated candidates and original actions                   |
| `/tools/velobank`             | Upload form; synthetic preview, mapping and export controls |
| `/settings/secrets`           | Metadata list, setup/unlock, creation/deletion dialogs      |
| `/profile`                    | Account details and actions                                 |
| `/users`                      | User list and existing creation dialog tests                |
| `/audit-log`                  | Filters and empty results                                   |

Existing matching tests exercise successive selections, failed refreshes,
interrupted jobs and confirmation/apply flows. Existing theme tests cover user
forms, errors and keyboard focus. Overview, shell, screening and statistics
retain their separate browser coverage; logout and the `/allegro` redirect are
unchanged server/navigation endpoints. Test artifacts are uploaded by Frontend
CI as `frontend-browser-checks`.
