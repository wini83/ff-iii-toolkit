<script lang="ts">
  import { onMount } from 'svelte';
  import WorkflowHeader from '#lib/components/WorkflowHeader.svelte';
  import { getDuplicateOptions, type DuplicateOptions } from '#lib/api/duplicates.js';
  import {
    compareStatement,
    reconcileMatches,
    type Evidence,
    type ReconciliationPreview,
    type ReconciliationApplyResult
  } from '#lib/api/reconciliation.js';

  let options: DuplicateOptions | null = null;
  let accountId = '';
  let from = '';
  let to = '';
  let tolerance = 0;
  let files: FileList | undefined;
  let completeStatement = false;
  let busy = false;
  let error: string | null = null;
  let preview: ReconciliationPreview | null = null;
  let submitted: Evidence | null = null;
  let selected = new Set<number>();
  let outcome: ReconciliationApplyResult | null = null;
  let filter = 'all';
  $: eligible =
    preview?.rows
      .filter(
        (row) =>
          row.status === 'matched' &&
          row.candidates[0].can_mark &&
          row.candidates[0].reconciled !== true
      )
      .map((row) => row.candidates[0].id) ?? [];
  $: rows = preview?.rows.filter((row) => filter === 'all' || row.status === filter) ?? [];

  onMount(() => {
    void loadOptions();
  });
  async function loadOptions() {
    busy = true;
    try {
      options = await getDuplicateOptions();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Could not load account options';
    } finally {
      busy = false;
    }
  }
  function toggle(id: number, checked: boolean) {
    const next = new Set(selected);
    if (checked) next.add(id);
    else next.delete(id);
    selected = next;
  }
  function invalidateComparison() {
    preview = null;
    submitted = null;
    selected = new Set();
    outcome = null;
  }
  async function compare(event: SubmitEvent) {
    event.preventDefault();
    if (!files?.[0] || !completeStatement) return;
    const evidence = { file: files[0], accountId, from, to, tolerance };
    busy = true;
    error = null;
    preview = null;
    submitted = null;
    selected = new Set();
    outcome = null;
    try {
      preview = await compareStatement(evidence);
      submitted = evidence;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Comparison failed';
    } finally {
      busy = false;
    }
  }
  async function apply() {
    if (!preview || !submitted || !selected.size || busy) return;
    const revision = preview.revision;
    const ids = [...selected];
    busy = true;
    error = null;
    // A request can commit even if its response is lost. Never reuse its preview.
    preview = null;
    selected = new Set();
    try {
      outcome = await reconcileMatches(submitted, revision, ids);
    } catch (e) {
      error = e instanceof Error ? e.message : 'Unknown write outcome. Compare again.';
    } finally {
      busy = false;
    }
  }
</script>

<div class="min-w-0 space-y-4">
  <WorkflowHeader title="Reconciliation assistant">
    <span slot="description">
      Compare a bank statement with Firefly and mark verified matches as reconciled.
    </span>
  </WorkflowHeader>
  {#if error}<div role="alert" class="alert alert-warning">{error}</div>{/if}
  {#if !options && !busy}<button class="btn btn-sm" on:click={loadOptions}>
      Retry accounts
    </button>{/if}
  <form class="card bg-base-100 p-4" on:submit={compare} on:change={invalidateComparison}>
    <fieldset disabled={busy || !options} class="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <legend class="sr-only">Statement comparison</legend>
      <label class="text-sm">
        Account<select class="select mt-1 w-full" bind:value={accountId} required>
          <option value="" disabled>Select an account</option>
          {#each options?.accounts ?? [] as account}<option value={String(account.id)}>
              {account.name}
            </option>{/each}
        </select>
      </label>
      <label class="text-sm">
        Period from<input
          class="input mt-1 w-full"
          type="date"
          bind:value={from}
          max={to || undefined}
          required
        />
      </label>
      <label class="text-sm">
        Period to<input
          class="input mt-1 w-full"
          type="date"
          bind:value={to}
          min={from || undefined}
          required
        />
      </label>
      <label class="text-sm">
        Date tolerance (days)<input
          class="input mt-1 w-full"
          type="number"
          min="0"
          max="2"
          step="1"
          bind:value={tolerance}
          required
        />
      </label>
      <label class="min-w-0 text-sm sm:col-span-2">
        Alior CSV (same format as BLIK enrichment)<input
          class="file-input mt-1 w-full"
          type="file"
          accept=".csv,text/csv"
          bind:files
          required
        />
      </label>
      <label class="flex items-center gap-2 text-sm sm:col-span-2">
        <input
          class="checkbox checkbox-sm"
          type="checkbox"
          bind:checked={completeStatement}
          required
        />This file includes all booked operations for this account and period.
      </label>
      <button class="btn btn-primary sm:col-span-2" type="submit">Compare statement</button>
    </fieldset>
    <p class="text-base-content/70 mt-3 text-xs">
      Maximum 2 MiB, 2000 rows and 366 days. UTF-8 Alior CSV; booking date is used when present. No
      uploaded file is retained on the server.
    </p>
  </form>
  {#if busy}<p role="status">Reading and verifying Firefly data…</p>{/if}
  {#if outcome}
    <section class="card bg-base-100 space-y-2 p-4" aria-label="Reconciliation outcomes">
      <h3 class="font-semibold">Marking results</h3>
      {#each outcome.outcomes as item}<p>#{item.id}: {item.status}</p>{/each}
      <p class="text-sm">
        Compare the statement again before another update. Unknown outcomes may already have been
        saved; the next comparison reads Firefly directly.
      </p>
    </section>
  {/if}
  {#if preview && submitted}
    <section class="min-w-0 space-y-3" aria-label="Statement comparison results">
      <p class="text-sm">
        Account #{submitted.accountId} · {submitted.from} – {submitted.to} · ±{submitted.tolerance} days
      </p>
      <div class="grid gap-2 sm:grid-cols-4">
        <p class="card bg-base-100 p-3">
          Matched: {preview.rows.filter((r) => r.status === 'matched').length}
        </p>
        <p class="card bg-base-100 p-3">
          Needs review: {preview.rows.filter((r) => r.status === 'review').length}
        </p>
        <p class="card bg-base-100 p-3">
          Bank only: {preview.rows.filter((r) => r.status === 'bank_only').length}
        </p>
        <p class="card bg-base-100 p-3">Firefly only: {preview.firefly_only.length}</p>
      </div>
      <p class="text-base-content/70 text-xs">
        One-to-one matches require the same signed account amount, currency and normalized title
        within the chosen date tolerance. Repeated identical payments stay ambiguous. Transfers are
        review-only because Firefly flags both account legs.
      </p>
      <p class="text-base-content/70 text-xs">
        Excluded: {preview.skipped_multipart} split groups, {preview.skipped_invalid} invalid transactions
        and {preview.excluded_foreign_transfers} incoming FX transfers. Totals compare represented movements,
        not bank opening or closing balances.
      </p>
      {#each preview.totals as total}<p class="toolkit-data text-sm">
          {total.currency}: bank {total.bank_total} · Firefly {total.firefly_total} · difference {total.difference}
        </p>{/each}
      <div class="flex flex-wrap items-center gap-3">
        <label class="flex items-center gap-2 text-sm">
          <input
            class="checkbox checkbox-sm"
            type="checkbox"
            checked={eligible.length > 0 && selected.size === eligible.length}
            disabled={busy || eligible.length === 0 || eligible.length > 100}
            on:change={(event) => {
              selected = event.currentTarget.checked ? new Set(eligible) : new Set();
            }}
          />Select verified matches
        </label>
        <button
          class="btn btn-sm btn-primary"
          disabled={busy || selected.size === 0 || selected.size > 100}
          on:click={apply}
        >
          Mark selected as reconciled ({selected.size})
        </button>
        <label class="text-sm">
          Show<select class="select select-sm ml-2" bind:value={filter}>
            <option value="all">All bank rows</option><option value="matched">
              Matched
            </option><option value="review">Needs review</option><option value="bank_only">
              Bank only
            </option>
          </select>
        </label>
      </div>
      <p class="text-base-content/70 text-xs">
        Select at most 100 per update. Marking only sets reconciled=true with rules and webhooks
        disabled; amounts, accounts and descriptions are not changed. Firefly protects reconciled
        amounts from editing.
      </p>
      <div class="card bg-base-100 min-w-0 overflow-x-auto">
        <table class="table-sm table">
          <caption class="sr-only">Bank statement comparison</caption>
          <thead>
            <tr>
              <th>Mark</th><th>Bank row</th><th>Amount</th><th>Title</th><th>Result</th><th>
                Firefly candidates
              </th>
            </tr>
          </thead>
          <tbody>
            {#each rows as row (row.bank.row_id)}
              <tr>
                <td>
                  {#if row.status === 'matched' && row.candidates[0].can_mark && row.candidates[0].reconciled !== true}<input
                      class="checkbox checkbox-sm"
                      type="checkbox"
                      aria-label={`Reconcile transaction #${row.candidates[0].id}`}
                      checked={selected.has(row.candidates[0].id)}
                      disabled={busy}
                      on:change={(event) =>
                        toggle(row.candidates[0].id, event.currentTarget.checked)}
                    />{/if}
                </td>
                <td class="toolkit-data whitespace-nowrap">
                  {row.bank.date} · #{row.bank.row_id}
                </td><td class="toolkit-data whitespace-nowrap">
                  {row.bank.amount}
                  {row.bank.currency}
                </td><td class="min-w-48 wrap-anywhere whitespace-normal">
                  {row.bank.description}
                </td><td>{row.status}</td>
                <td class="min-w-64 whitespace-normal">
                  {#each row.candidates as tx}<p>
                      #{tx.id} · {tx.date} · {tx.description}{tx.reconciled
                        ? ' · Already reconciled'
                        : ''}{tx.restriction ? ` · ${tx.restriction}` : ''}
                    </p>{/each}{#if row.candidate_count > 5}<p>
                      Showing 5 of {row.candidate_count} candidates
                    </p>{/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <h3 class="font-semibold">Only in Firefly</h3>
      <div class="card bg-base-100 min-w-0 overflow-x-auto">
        <table class="table-sm table">
          <caption class="sr-only">
            Firefly transactions without statement candidates
          </caption><thead>
            <tr><th>ID</th><th>Date</th><th>Amount</th><th>Title</th></tr>
          </thead><tbody>
            {#each preview.firefly_only as tx}<tr>
                <td>#{tx.id}</td><td class="whitespace-nowrap">{tx.date}</td><td
                  class="whitespace-nowrap"
                >
                  {tx.amount}
                  {tx.currency}
                </td><td class="min-w-40 wrap-anywhere whitespace-normal">{tx.description}</td>
              </tr>{:else}<tr><td colspan="4">No Firefly-only transactions.</td></tr>{/each}
          </tbody>
        </table>
      </div>
    </section>
  {/if}
</div>
