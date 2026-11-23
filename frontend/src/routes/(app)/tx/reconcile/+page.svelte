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
  $: rows =
    preview?.rows.filter(
      (row) => filter === 'all' || row.status === filter || row.review_reason === filter
    ) ?? [];
  $: titleMismatches =
    preview?.rows.filter((row) => row.review_reason === 'title_mismatch').length ?? 0;
  $: ambiguous = preview?.rows.filter((row) => row.review_reason === 'ambiguous').length ?? 0;

  function displayAmount(value: string) {
    if (/^[+-]?0(?:\.0+)?(?:e[+-]?\d+)?$/i.test(value)) return '0.00';
    const [whole, fraction = ''] = value.split('.');
    if (!/^-?\d+$/.test(whole) || !/^\d*$/.test(fraction)) return value;
    return whole + '.' + fraction.replace(/0+$/, '').padEnd(2, '0');
  }

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
        {options?.accounts.find((account) => String(account.id) === submitted?.accountId)?.name ??
          `Account #${submitted.accountId}`} · {submitted.from} – {submitted.to} · ±{submitted.tolerance}
        days
      </p>
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <p class="card bg-base-100 p-3">
          Matched: {preview.rows.filter((r) => r.status === 'matched').length}
        </p>
        <p class="card bg-base-100 p-3">
          Needs review: {preview.rows.filter((r) => r.status === 'review').length}
          {#if titleMismatches || ambiguous}
            <span class="text-base-content/70 text-xs">
              {titleMismatches} different titles · {ambiguous} ambiguous
            </span>
          {/if}
        </p>
        <p class="card bg-base-100 p-3">
          Bank only: {preview.rows.filter((r) => r.status === 'bank_only').length}
        </p>
        <p class="card bg-base-100 p-3">Firefly only: {preview.firefly_only.length}</p>
      </div>
      {#each preview.totals as total (total.currency)}
        <section
          class="card bg-base-100 gap-2 p-3"
          aria-label={`Movement totals in ${total.currency}`}
        >
          <p class="font-medium">Net movements · {total.currency}</p>
          <dl class="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
            <dt>Bank</dt>
            <dd class="toolkit-data text-right">{displayAmount(total.bank_total)}</dd>
            <dt>Firefly</dt>
            <dd class="toolkit-data text-right">{displayAmount(total.firefly_total)}</dd>
            <dt class="font-semibold">Difference</dt>
            <dd class="toolkit-data text-right font-semibold">{displayAmount(total.difference)}</dd>
          </dl>
        </section>
      {/each}
      {#if preview.skipped_multipart || preview.skipped_invalid || preview.excluded_foreign_transfers}
        <p class="text-base-content/70 text-xs">
          Excluded: {preview.skipped_multipart} split groups, {preview.skipped_invalid} invalid transactions
          and {preview.excluded_foreign_transfers} incoming FX transfers.
        </p>
      {/if}
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
            <option value="all">All transactions</option><option value="matched"> Matched </option>
            <option value="review">Needs review</option>
            <option value="title_mismatch">Different title ({titleMismatches})</option>
            <option value="ambiguous">Ambiguous ({ambiguous})</option>
            <option value="bank_only"> Bank only </option>
            <option value="firefly_only">Firefly only</option>
          </select>
        </label>
      </div>
      <details class="text-base-content/70 text-xs">
        <summary class="cursor-pointer">How matches and differences are calculated</summary>
        <p class="mt-1">
          Candidates share the same signed amount and currency within your date tolerance. Verified
          matches also have the same normalized title and match one-to-one. Transfers are read-only
          here because Firefly flags both account legs. Net movements exclude opening and closing
          balances. You can mark up to 100 verified matches per update.
        </p>
      </details>
      {#if filter !== 'firefly_only'}
        <ul class="space-y-2" aria-label="Bank statement comparison">
          {#each rows as row (row.bank.row_id)}
            <li class="card bg-base-100 gap-2 p-3">
              <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
                {#if row.status === 'matched' && row.candidates[0].can_mark && row.candidates[0].reconciled !== true}
                  <input
                    class="checkbox checkbox-sm"
                    type="checkbox"
                    aria-label={`Reconcile transaction #${row.candidates[0].id}`}
                    checked={selected.has(row.candidates[0].id)}
                    disabled={busy}
                    on:change={(event) => toggle(row.candidates[0].id, event.currentTarget.checked)}
                  />
                {/if}
                <span class="toolkit-data font-semibold">
                  {displayAmount(row.bank.amount)}
                  {row.bank.currency}
                </span>
                <span class="text-sm">
                  {row.status === 'matched'
                    ? 'Matched'
                    : row.status === 'bank_only'
                      ? 'Bank only'
                      : row.review_reason === 'title_mismatch'
                        ? 'Different title'
                        : row.review_reason === 'ambiguous'
                          ? 'Ambiguous'
                          : 'Needs review'}
                </span>
              </div>
              <div class="grid min-w-0 gap-2 sm:grid-cols-2">
                <div class="bg-base-200/50 min-w-0 rounded p-2">
                  <p class="text-base-content/70 text-xs">
                    Bank · {row.bank.date} · row {row.bank.row_id}
                  </p>
                  <p class="text-sm wrap-anywhere">{row.bank.description}</p>
                </div>
                <div class="bg-base-200/50 min-w-0 space-y-2 rounded p-2">
                  {#each row.candidates as tx (tx.id)}
                    <div>
                      <p class="text-base-content/70 text-xs">
                        Firefly · {tx.date} · #{tx.id}
                        {#if tx.date !== row.bank.date}<span
                            class="text-base-content font-semibold"
                          >
                            · Different date
                          </span>{/if}
                      </p>
                      <p
                        class="text-sm wrap-anywhere"
                        class:font-semibold={tx.title_matches === false}
                      >
                        {tx.description}
                        {#if tx.title_matches === false}<span class="text-xs">
                            · Different title
                          </span>{/if}
                      </p>
                      {#if tx.reconciled || tx.restriction}
                        <p class="text-base-content/70 text-xs">
                          {tx.reconciled ? 'Already reconciled' : tx.restriction}
                        </p>
                      {/if}
                    </div>
                  {:else}
                    <p class="text-base-content/70 text-sm">No Firefly candidate</p>
                  {/each}
                  {#if row.candidate_count > row.candidates.length}
                    <p class="text-xs">
                      Showing {row.candidates.length} of {row.candidate_count} candidates
                    </p>
                  {/if}
                </div>
              </div>
            </li>
          {:else}
            <li class="text-base-content/70 text-sm">No transactions in this view.</li>
          {/each}
        </ul>
      {/if}
      {#if filter === 'all' || filter === 'firefly_only'}
        <section class="space-y-2" aria-label="Firefly-only transactions">
          <h3 class="font-semibold">Only in Firefly</h3>
          <ul class="space-y-2">
            {#each preview.firefly_only as tx (tx.id)}
              <li class="card bg-base-100 gap-1 p-3">
                <p class="toolkit-data font-semibold">{displayAmount(tx.amount)} {tx.currency}</p>
                <p class="text-base-content/70 text-xs">{tx.date} · #{tx.id}</p>
                <p class="text-sm wrap-anywhere">{tx.description}</p>
              </li>
            {:else}
              <li class="text-base-content/70 text-sm">No Firefly-only transactions.</li>
            {/each}
          </ul>
        </section>
      {/if}
    </section>
  {/if}
</div>
