<script lang="ts">
  import { onMount } from 'svelte';
  import OperationGroups from '#lib/components/OperationGroups.svelte';
  import ReconciliationGroups from '#lib/components/ReconciliationGroups.svelte';
  import WorkflowHeader from '#lib/components/WorkflowHeader.svelte';
  import { getDuplicateOptions, type DuplicateOptions } from '#lib/api/duplicates.js';
  import {
    compareStatement,
    inspectStatementPeriod,
    pendingGroupIds,
    reconciliationBatches,
    reconcileMatches,
    type Evidence,
    type ReconciliationPreview,
    type ReconciliationApplyResult,
    type StatementPeriod
  } from '#lib/api/reconciliation.js';

  let options: DuplicateOptions | null = null;
  let accountId = '';
  let from = '';
  let to = '';
  let tolerance = 0;
  let statementFile: File | undefined;
  let period: StatementPeriod | undefined;
  let confirmDialog: HTMLDialogElement;
  let busy = false;
  let error: string | null = null;
  let preview: ReconciliationPreview | null = null;
  let submitted: Evidence | null = null;
  let selected = new Set<number>();
  let selectedGroups = new Set<string>();
  let outcome: ReconciliationApplyResult | null = null;
  let progress: { current: number; total: number } | null = null;
  let unsentBatches = 0;
  let filter = 'all';
  $: rows =
    preview?.rows.filter(
      (row) => filter === 'all' || row.status === filter || row.review_reason === filter
    ) ?? [];
  $: eligible = rows
    .filter(
      (row) =>
        row.status === 'matched' &&
        row.candidates[0].can_mark &&
        row.candidates[0].reconciled !== true
    )
    .map((row) => row.candidates[0].id);
  $: selectedIds = eligible.filter((id) => selected.has(id));
  $: groups = preview?.reconciliation_groups ?? [];
  $: readyGroups = groups.filter((group) => pendingGroupIds(group).length);
  $: readyCount = readyGroups.reduce((count, group) => count + pendingGroupIds(group).length, 0);
  $: selectedGroupEntries = readyGroups.filter((group) => selectedGroups.has(group.id));
  $: selectedCount = new Set([...selectedIds, ...selectedGroupEntries.flatMap(pendingGroupIds)])
    .size;
  $: titleMismatches =
    preview?.rows.filter((row) => row.review_reason === 'title_mismatch').length ?? 0;
  $: ambiguous = preview?.rows.filter((row) => row.review_reason === 'ambiguous').length ?? 0;
  $: normalized =
    preview?.rows.filter((row) => row.review_reason === 'description_normalization').length ?? 0;

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
  function toggleGroup(id: string, checked: boolean) {
    const next = new Set(selectedGroups);
    if (checked) next.add(id);
    else next.delete(id);
    selectedGroups = next;
  }
  function invalidateComparison() {
    preview = null;
    submitted = null;
    selected = new Set();
    selectedGroups = new Set();
    outcome = null;
    unsentBatches = 0;
  }
  async function loadStatement(file: File | undefined) {
    statementFile = file;
    period = undefined;
    from = '';
    to = '';
    error = null;
    invalidateComparison();
    if (!file) return;
    busy = true;
    try {
      period = await inspectStatementPeriod(file);
      from = period.date_from;
      to = period.date_to;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Could not read the statement period';
    } finally {
      busy = false;
    }
    if (period && accountId) await compare();
  }

  function changeAccount(id: string) {
    accountId = id;
    invalidateComparison();
    if (period) void compare();
  }

  async function compare() {
    if (!statementFile || !accountId || busy) return;
    if (!period) {
      await loadStatement(statementFile);
      return;
    }
    if (!from || !to) return;
    const evidence = { file: statementFile, accountId, from, to, tolerance };
    busy = true;
    error = null;
    invalidateComparison();
    try {
      preview = await compareStatement(evidence);
      submitted = evidence;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Comparison failed';
    } finally {
      busy = false;
    }
  }
  function requestApply() {
    if (selectedGroupEntries.length || selectedCount > 1) confirmDialog.showModal();
    else void apply(false);
  }

  async function apply(statementComplete: boolean) {
    if (!preview || !submitted || !selectedCount || busy) return;
    const evidence = submitted;
    const revision = preview.revision;
    const batches = reconciliationBatches(selectedIds, selectedGroupEntries);
    const refreshAfterSave = selectedGroupEntries.length > 0;
    busy = true;
    error = null;
    outcome = null;
    unsentBatches = 0;
    // A request can commit even if its response is lost. Never reuse its preview.
    preview = null;
    selected = new Set();
    selectedGroups = new Set();
    try {
      const outcomes: ReconciliationApplyResult['outcomes'] = [];
      for (const [index, batch] of batches.entries()) {
        progress = { current: index + 1, total: batches.length };
        unsentBatches = batches.length - index - 1;
        const result = await reconcileMatches(
          evidence,
          revision,
          batch.ids,
          batch.groupIds,
          statementComplete
        );
        outcomes.push(...result.outcomes);
        outcome = { outcomes: [...outcomes] };
        if (
          result.outcomes.some(
            (item) => item.status === 'unknown' || item.status === 'not_processed'
          )
        )
          return;
      }
      progress = null;
      if (refreshAfterSave) {
        try {
          preview = await compareStatement(evidence);
        } catch {
          error = 'Zapis zakończony. Porównaj wyciąg ponownie, aby odświeżyć widok.';
        }
      }
    } catch (e) {
      error = e instanceof Error ? e.message : 'Unknown write outcome. Compare again.';
    } finally {
      busy = false;
      progress = null;
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
  <form class="card bg-base-100 p-4" on:submit|preventDefault={compare}>
    <fieldset disabled={busy || !options} class="grid min-w-0 gap-3 sm:grid-cols-2">
      <legend class="sr-only">Statement comparison</legend>
      <label class="text-sm">
        Account<select
          class="select mt-1 w-full"
          value={accountId}
          on:change={(event) => changeAccount(event.currentTarget.value)}
          required
        >
          <option value="" disabled>Select an account</option>
          {#each options?.accounts ?? [] as account}<option value={String(account.id)}>
              {account.name}
            </option>{/each}
        </select>
      </label>
      <label class="min-w-0 text-sm">
        Alior CSV (same format as BLIK enrichment)<input
          class="file-input mt-1 w-full"
          type="file"
          accept=".csv,text/csv"
          on:change={(event) => loadStatement(event.currentTarget.files?.[0])}
          required
        />
      </label>
      {#if period}
        <div class="sm:col-span-2">
          <p class="text-sm">
            Okres porównania <strong>{from} – {to}</strong>
            <span class="text-base-content/70">
              · {from !== period.date_from || to !== period.date_to
                ? 'Ustawiony ręcznie'
                : period.source === 'metadata'
                  ? 'Z nagłówka wyciągu'
                  : 'Od pierwszej do ostatniej operacji w CSV'}
            </span>
          </p>
          <details class="mt-1">
            <summary class="cursor-pointer text-sm">Zmień okres lub tolerancję</summary>
            <div class="mt-2 grid gap-2 sm:grid-cols-3">
              <label class="text-sm">
                Period from<input
                  class="input mt-1 w-full"
                  type="date"
                  bind:value={from}
                  max={to || undefined}
                  on:change={invalidateComparison}
                  required
                />
              </label>
              <label class="text-sm">
                Period to<input
                  class="input mt-1 w-full"
                  type="date"
                  bind:value={to}
                  min={from || undefined}
                  on:change={invalidateComparison}
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
                  on:change={invalidateComparison}
                  required
                />
              </label>
            </div>
          </details>
        </div>
      {/if}
      <button
        class="btn btn-primary sm:col-span-2"
        type="submit"
        disabled={!statementFile || !accountId}
      >
        Compare statement
      </button>
    </fieldset>
    <p class="text-base-content/70 mt-3 text-xs">
      Maximum 2 MiB, 2000 rows and 366 days. UTF-8 Alior CSV; booking date is used when present. No
      uploaded file is retained on the server.
    </p>
  </form>
  {#if progress}
    <p role="status">Uzgadnianie · partia {progress.current} z {progress.total}</p>
  {:else if busy && statementFile && !period}
    <p role="status">Odczytywanie okresu z CSV…</p>
  {:else if busy}
    <p role="status">Reading and verifying Firefly data…</p>
  {/if}
  {#if !busy && unsentBatches}
    <p class="text-sm">
      Nie wysłano kolejnych partii ({unsentBatches}). Porównaj wyciąg ponownie przed wznowieniem.
    </p>
  {/if}
  {#if outcome}
    <section class="card bg-base-100 space-y-2 p-4" aria-label="Reconciliation outcomes">
      <h3 class="font-semibold">Marking results</h3>
      <p class="text-sm">
        Uzgodnione ({outcome.outcomes.filter((item) => item.status === 'reconciled').length}) · Już
        uzgodnione ({outcome.outcomes.filter((item) => item.status === 'already_reconciled')
          .length}) · Do sprawdzenia ({outcome.outcomes.filter(
          (item) => item.status === 'unknown' || item.status === 'not_processed'
        ).length})
      </p>
      <details open={outcome.outcomes.length <= 10}>
        <summary class="cursor-pointer text-sm">Szczegóły zapisu</summary>
        {#each outcome.outcomes as item}<p>#{item.id}: {item.status}</p>{/each}
      </details>
      {#if !busy && !preview}
        <p class="text-sm">
          Compare the statement again before another update. Unknown outcomes may already have been
          saved; the next comparison reads Firefly directly.
        </p>
      {/if}
    </section>
  {/if}
  {#if preview && submitted}
    <section class="min-w-0 space-y-3" aria-label="Statement comparison results">
      <p class="text-sm">
        {options?.accounts.find((account) => String(account.id) === submitted?.accountId)?.name ??
          `Account #${submitted.accountId}`} · {submitted.from} – {submitted.to} · ±{submitted.tolerance}
        days
      </p>
      {#if preview.skipped_zero_amount}
        <p class="text-base-content/70 text-sm">
          Pominięto operacje CSV o kwocie 0 ({preview.skipped_zero_amount}).
        </p>
      {/if}
      <div class="card bg-base-100 flex-row flex-wrap items-center gap-3 p-4">
        {#if groups.length}
          <label class="flex items-center gap-2 text-sm">
            <input
              class="checkbox checkbox-sm"
              type="checkbox"
              checked={readyGroups.length > 0 && selectedGroupEntries.length === readyGroups.length}
              disabled={busy || readyGroups.length === 0}
              on:change={(event) => {
                selectedGroups = new Set(
                  event.currentTarget.checked ? readyGroups.map((group) => group.id) : []
                );
              }}
            />
            Zaznacz wszystkie zgodne ({readyCount})
          </label>
        {/if}
        <button
          class="btn btn-sm btn-primary"
          disabled={busy || selectedCount === 0}
          on:click={requestApply}
        >
          Mark selected as reconciled ({selectedCount})
        </button>
      </div>
      {#if groups.length}
        <ReconciliationGroups {groups} {busy} {selectedGroups} onToggle={toggleGroup} />
      {/if}
      <details open={!groups.length} class="space-y-3">
        <summary class="cursor-pointer text-sm font-medium">
          Analiza opisów i uzgadnianie pojedyncze
        </summary>
        <OperationGroups groups={preview.operation_groups ?? []} />
        <h2 class="pt-2 text-sm font-semibold">Oznaczanie zweryfikowanych uzgodnień</h2>
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
              <dd class="toolkit-data text-right font-semibold">
                {displayAmount(total.difference)}
              </dd>
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
              checked={eligible.length > 0 && selectedIds.length === eligible.length}
              disabled={busy || eligible.length === 0}
              on:change={(event) => {
                selected = event.currentTarget.checked ? new Set(eligible) : new Set();
              }}
            />Select verified matches
          </label>
          <label class="text-sm">
            Show<select
              class="select select-sm ml-2"
              bind:value={filter}
              on:change={() => (selected = new Set())}
            >
              <option value="all">All transactions</option><option value="matched">
                Matched
              </option>
              <option value="review">Needs review</option>
              <option value="title_mismatch">Different title ({titleMismatches})</option>
              <option value="description_normalization">
                Zgodny punkt sprzedaży ({normalized})
              </option>
              <option value="ambiguous">Ambiguous ({ambiguous})</option>
              <option value="bank_only"> Bank only </option>
              <option value="firefly_only">Firefly only</option>
            </select>
          </label>
        </div>
        <details class="text-base-content/70 text-xs">
          <summary class="cursor-pointer">How matches and differences are calculated</summary>
          <p class="mt-1">
            Groups use the exact booking day, currency and signed amount. Individual matches also
            require a one-to-one match by normalized title within the selected date tolerance.
            Transfers require manual review because Firefly flags both account legs. Net movements
            exclude opening and closing balances. Large selections are saved in successive batches.
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
                      on:change={(event) =>
                        toggle(row.candidates[0].id, event.currentTarget.checked)}
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
                          : row.review_reason === 'description_normalization'
                            ? 'Zgodny punkt sprzedaży'
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
                    <p class="text-sm wrap-anywhere whitespace-pre-wrap">{row.bank.description}</p>
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
                          class="text-sm wrap-anywhere whitespace-pre-wrap"
                          class:font-semibold={tx.title_matches === false}
                        >
                          {tx.description}
                          {#if tx.title_matches === false && !tx.normalized_description_key}<span
                              class="text-xs"
                            >
                              · Different title
                            </span>{/if}
                        </p>
                        {#if tx.normalized_description_key}
                          <p class="text-base-content/70 mt-1 text-xs">
                            Preview only. After removing the debit-card prefix, the remaining
                            Firefly description matches the start of the bank description.
                          </p>
                          <p class="text-xs wrap-anywhere">
                            Compared key <code>{tx.normalized_description_key}</code>
                          </p>
                        {/if}
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
      </details>
    </section>
  {/if}
</div>

<dialog class="modal" bind:this={confirmDialog} aria-labelledby="confirm-statement-title">
  <div class="modal-box">
    <h2 id="confirm-statement-title" class="text-lg font-semibold">
      Potwierdź kompletność wyciągu
    </h2>
    <p class="mt-2 text-sm">
      Czy CSV zawiera wszystkie zaksięgowane operacje wybranego rachunku za okres
      {submitted?.from} – {submitted?.to}?
    </p>
    {#if period?.source === 'transactions' && submitted?.from === period.date_from && submitted?.to === period.date_to}
      <p class="text-base-content/70 mt-2 text-sm">
        Okres ustalono na podstawie dat operacji. Możesz go zmienić przed zapisem, jeśli wyciąg
        obejmuje też dni bez operacji.
      </p>
    {/if}
    <div class="modal-action">
      <button type="button" class="btn" on:click={() => confirmDialog.close()}>Anuluj</button>
      <button
        type="button"
        class="btn btn-primary"
        on:click={() => {
          confirmDialog.close();
          void apply(true);
        }}
      >
        Potwierdzam, uzgodnij ({selectedCount})
      </button>
    </div>
  </div>
</dialog>
