<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import WorkflowHeader from '#lib/components/WorkflowHeader.svelte';
  import { applyTag } from '#lib/api/tx.js';
  import type { components } from '#lib/api/schema.js';
  import { ApiError } from '#lib/api/errors.js';
  import {
    getDuplicateOptions,
    refreshDuplicateOptions,
    getDuplicateResults,
    type DuplicateOptions,
    type DuplicateResults,
    type DuplicateQuery
  } from '#lib/api/duplicates.js';

  const deletionTag = 'do-usuniecia';
  type Pair = DuplicateResults['items'][number];
  let decisions = new Map<string, number | 'both'>();
  let selectedIds = new Set<number>();
  let keptIds = new Set<number>();
  let confirmedKeepIds = new Set<number>();
  let reviewDialog: HTMLDialogElement;
  let markedIds = new Set<number>();
  let marking = false;
  let markError: string | null = null;
  let markMessage: string | null = null;

  function isMarked(tx: components['schemas']['SimplifiedTx']): boolean {
    return tx.tags.includes(deletionTag) || markedIds.has(tx.id);
  }

  function chooseKeep(pair: Pair, keep: number | 'both' | null) {
    if (busy || marking || !result) return;
    const next = new Map(decisions);
    if (keep === null) next.delete(pair.id);
    else next.set(pair.id, keep);
    const kept = new Set(confirmedKeepIds);
    const removed = new Set<number>();
    for (const candidate of result.items) {
      const choice = next.get(candidate.id);
      if (choice === undefined) continue;
      for (const tx of [candidate.first, candidate.second]) {
        if (choice === 'both' || choice === tx.id) kept.add(tx.id);
        else if (!isMarked(tx)) removed.add(tx.id);
      }
    }
    const conflict = [...removed].find((txId) => kept.has(txId));
    if (conflict !== undefined) {
      markError = `Transaction #${conflict} is kept in another pair. Clear conflicting pending decisions before marking it for deletion. Copies kept in a confirmed batch remain protected during this session.`;
      return;
    }
    decisions = next;
    keptIds = kept;
    selectedIds = removed;
    markError = null;
    markMessage = null;
  }

  function clearDecisions() {
    decisions = new Map();
    selectedIds = new Set();
    keptIds = new Set(confirmedKeepIds);
    markError = null;
    markMessage = null;
  }

  function pendingTransactions(results: DuplicateResults | null, ids: Set<number>) {
    const unique = new Map<number, components['schemas']['SimplifiedTx']>();
    for (const pair of results?.items ?? []) {
      for (const tx of [pair.first, pair.second]) {
        if (ids.has(tx.id)) unique.set(tx.id, tx);
      }
    }
    return [...unique.values()];
  }

  async function markSelected() {
    if (busy || marking || selectedIds.size === 0) return;
    reviewDialog.close();
    const id = requestId;
    const ids = [...selectedIds];
    marking = true;
    markError = null;
    markMessage = null;
    let succeeded = 0;
    const failed: number[] = [];
    try {
      for (const txId of ids) {
        if (id !== requestId) return;
        try {
          await applyTag(txId, deletionTag);
          if (id !== requestId) return;
          markedIds = new Set([...markedIds, txId]);
          confirmedKeepIds = new Set([...confirmedKeepIds, ...keptIds]);
          const next = new Set(selectedIds);
          next.delete(txId);
          selectedIds = next;
          succeeded += 1;
        } catch {
          if (id !== requestId) return;
          failed.push(txId);
        }
      }
      markMessage = `Marked ${succeeded} transaction(s) with ${deletionTag}.`;
      if (failed.length > 0) {
        markError = `Could not mark transaction(s) ${failed.map((txId) => `#${txId}`).join(', ')}. They remain selected; retry marking them.`;
      }
    } finally {
      if (id === requestId) marking = false;
    }
  }

  let options: DuplicateOptions | null = null;
  let result: DuplicateResults | null = null;
  let submitted: DuplicateQuery | null = null;
  let accountId = '';
  let dateFrom = '';
  let dateTo = '';
  let tolerance = 2;
  let busy = true;
  let error: string | null = null;
  let changed = false;
  let requestId = 0;

  onMount(() => {
    void loadOptions();
  });
  onDestroy(() => {
    requestId += 1;
  });

  async function loadOptions(forceRefresh = false) {
    const id = ++requestId;
    clearDecisions();
    busy = true;
    error = null;
    changed = false;
    result = null;
    submitted = null;
    try {
      const next = await (forceRefresh ? refreshDuplicateOptions() : getDuplicateOptions());
      if (id !== requestId) return;
      options = next;
      if (forceRefresh) markedIds = new Set();
      if (!next.accounts.some((account) => String(account.id) === accountId)) accountId = '';
      dateFrom ||= next.date_from ?? '';
      dateTo ||= next.date_to ?? '';
    } catch (e) {
      if (id === requestId) error = e instanceof Error ? e.message : 'Failed to load transactions';
    } finally {
      if (id === requestId) busy = false;
    }
  }

  async function search(offset = 0, query = submitted) {
    if (!query) return;
    const id = ++requestId;
    clearDecisions();
    busy = true;
    error = null;
    changed = false;
    result = null;
    submitted = query;
    try {
      const next = await getDuplicateResults(query, offset);
      if (id === requestId) result = next;
    } catch (e) {
      if (id !== requestId) return;
      changed = e instanceof ApiError && e.status === 409;
      error = changed
        ? 'The transaction snapshot changed. Reload transactions and search again.'
        : e instanceof Error
          ? e.message
          : 'Failed to find potential duplicates';
    } finally {
      if (id === requestId) busy = false;
    }
  }

  function submit(event: SubmitEvent) {
    event.preventDefault();
    if (!options || !accountId || !dateFrom || !dateTo) return;
    void search(0, {
      account_id: Number(accountId),
      date_from: dateFrom,
      date_to: dateTo,
      tolerance_days: tolerance,
      snapshot_timestamp: options.snapshot_timestamp
    });
  }

  function fireflyLink(base: string | null, id: number): string | null {
    if (!base) return null;
    try {
      const url = new URL(base);
      if (!['http:', 'https:'].includes(url.protocol)) return null;
      url.pathname = `${url.pathname.replace(/\/$/, '')}/transactions/show/${id}`;
      url.search = '';
      url.hash = '';
      return url.toString();
    } catch {
      return null;
    }
  }
</script>

<div class="min-w-0 space-y-4">
  <WorkflowHeader title="Potential duplicates">
    <span slot="description"
      >Compare transactions on one account with exactly the same amount and currency, different tag
      sets and nearby dates.</span
    >
    <button
      slot="actions"
      class="btn btn-sm"
      disabled={busy || marking}
      on:click={() => loadOptions(true)}>Reload transactions</button
    >
  </WorkflowHeader>

  {#if options}
    <form class="card bg-base-100 p-4" on:submit={submit}>
      <fieldset
        disabled={busy || marking || options.accounts.length === 0}
        class="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-5"
      >
        <legend class="sr-only">Duplicate search filters</legend>
        <label class="min-w-0 text-sm sm:col-span-2 xl:col-span-1"
          >Account
          <select class="select mt-1 w-full" bind:value={accountId} required>
            <option value="" disabled>Select an account</option>
            {#each options.accounts as account}<option value={String(account.id)}
                >{account.name}</option
              >{/each}
          </select>
        </label>
        <label class="min-w-0 text-sm"
          >Date from
          <input
            class="input mt-1 w-full"
            type="date"
            bind:value={dateFrom}
            max={dateTo || undefined}
            required
          />
        </label>
        <label class="min-w-0 text-sm"
          >Date to
          <input
            class="input mt-1 w-full"
            type="date"
            bind:value={dateTo}
            min={dateFrom || undefined}
            required
          />
        </label>
        <label class="min-w-0 text-sm"
          >Date tolerance (days)
          <input
            class="input mt-1 w-full"
            type="number"
            min="0"
            max="7"
            step="1"
            bind:value={tolerance}
            required
          />
        </label>
        <button class="btn btn-primary self-end" type="submit">Find candidates</button>
      </fieldset>
      <p class="text-base-content/70 mt-3 text-xs">
        Same direction on the account; transaction types may differ. Tolerance is the maximum
        difference between the two dates (0 = same day). Both dates must be within the selected
        range.
      </p>
      <p class="text-base-content/70 mt-1 text-xs">
        Partially overlapping tags and empty vs nonempty tags qualify. Identical tag sets, including
        two empty sets, do not. Descriptions are for review only.
      </p>
      {#if options.accounts.length === 0}<p class="mt-3">
          No asset accounts in the transaction snapshot.
        </p>{/if}
    </form>
    <p class="text-base-content/70 text-xs">
      Snapshot: {options.snapshot_timestamp}. Split groups excluded: {options.skipped_multipart}.
      Incoming foreign-currency transfers are excluded because their account amount is unavailable.
    </p>
  {/if}

  {#if busy}<p role="status">{options ? 'Loading…' : 'Loading transactions…'}</p>{/if}
  {#if error}
    <div class="alert alert-warning" role="alert">
      <span>{error}</span>
      <button
        class="btn btn-sm"
        disabled={busy || marking}
        on:click={() =>
          changed || !submitted ? loadOptions(!changed && options !== null) : search()}
        >{changed || !submitted ? 'Reload transactions and filters' : 'Retry search'}</button
      >
    </div>
  {/if}
  {#if result && submitted}
    <section aria-label="Duplicate candidates" class="min-w-0 space-y-3">
      <div
        class="border-base-300 flex flex-wrap items-baseline justify-between gap-2 border-b pb-2"
      >
        <h3 class="text-base font-semibold">Candidate pairs: {result.total}</h3>
        <p class="text-base-content/70 text-xs">
          {options?.accounts.find((account) => account.id === submitted?.account_id)?.name} · {submitted.date_from}
          – {submitted.date_to} · ±{submitted.tolerance_days} days
        </p>
      </div>
      <p class="text-base-content/70 text-sm">
        These are review candidates, including possible legitimate repeated payments. Select the
        copy to keep in each pair, or keep both. Review your decisions before adding
        <code>{deletionTag}</code> to the other copies. Existing tags are preserved. Decisions apply to
        this page only; nothing is selected automatically.
      </p>
      {#if result.items.length > 0}
        <div
          class="border-base-300 bg-base-100 sticky top-2 z-10 flex flex-wrap items-center gap-3 rounded border p-3"
        >
          <span class="text-sm">Keep: {keptIds.size} · To mark: {selectedIds.size}</span>
          <button
            class="btn btn-sm btn-primary"
            disabled={busy || marking || selectedIds.size === 0}
            on:click={() => reviewDialog.showModal()}
            >{marking ? 'Marking…' : `Review decisions (${selectedIds.size})`}</button
          >
          <button
            class="btn btn-sm btn-ghost"
            disabled={marking || selectedIds.size === 0}
            on:click={clearDecisions}>Clear decisions</button
          >
        </div>
      {/if}
      {#if markMessage}<p role="status" class="text-sm">{markMessage}</p>{/if}
      {#if markError}<div class="alert alert-warning" role="alert">{markError}</div>{/if}
      {#if result.total === 0}<p>No potential duplicates for these filters.</p>{/if}
      {#each result.items as pair (pair.id)}
        <article class="card bg-base-100 min-w-0 overflow-hidden" aria-label={`Pair ${pair.id}`}>
          <div
            class="border-base-300 flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3"
          >
            <h4 class="toolkit-data text-sm font-semibold">
              {pair.amount}
              {pair.currency_code} · {pair.direction === 'incoming' ? 'Incoming' : 'Outgoing'}
            </h4>
            <span class="text-base-content/70 text-xs"
              >Date difference: {pair.date_difference_days} days · Different tag sets</span
            >
            <div class="flex gap-2">
              <button
                class="btn btn-xs"
                disabled={busy || marking || isMarked(pair.first) || isMarked(pair.second)}
                on:click={() => chooseKeep(pair, 'both')}>Keep both</button
              >
              <button
                class="btn btn-xs btn-ghost"
                disabled={busy || marking || !decisions.has(pair.id)}
                on:click={() => chooseKeep(pair, null)}>Clear pair decision</button
              >
            </div>
          </div>
          <div class="overflow-x-auto">
            <table class="table-sm table">
              <caption class="sr-only">Transactions in candidate pair {pair.id}</caption>
              <thead
                ><tr
                  ><th>Decision</th><th>Date</th><th>Description</th><th>Type</th><th
                    >Source account</th
                  ><th>Destination account</th><th>Tags</th><th>Firefly</th></tr
                ></thead
              >
              <tbody>
                {#each [pair.first, pair.second] as tx (tx.id)}
                  {@const link = fireflyLink(result.firefly_url, tx.id)}
                  <tr class:bg-base-200={selectedIds.has(tx.id)}>
                    <td>
                      {#if isMarked(tx)}
                        <span class="badge badge-outline badge-sm whitespace-nowrap"
                          >Marked for deletion</span
                        >
                      {:else}
                        <div class="flex flex-col items-start gap-1">
                          <button
                            class="btn btn-xs"
                            class:btn-primary={keptIds.has(tx.id)}
                            aria-label={`Keep transaction #${tx.id}`}
                            aria-pressed={keptIds.has(tx.id)}
                            disabled={busy || marking}
                            on:click={() => chooseKeep(pair, tx.id)}>Keep this</button
                          >
                          {#if selectedIds.has(tx.id)}
                            <span class="badge badge-outline badge-sm whitespace-nowrap"
                              >To mark for deletion</span
                            >
                          {:else if keptIds.has(tx.id)}
                            <span class="text-base-content/70 text-xs">Keep</span>
                          {/if}
                        </div>
                      {/if}
                    </td>
                    <td class="toolkit-data whitespace-nowrap">{tx.date}</td>
                    <td class="min-w-40 wrap-anywhere whitespace-normal">{tx.description}</td>
                    <td>{tx.type}</td><td>{tx.source_account?.name ?? '—'}</td><td
                      >{tx.destination_account?.name ?? '—'}</td
                    >
                    <td
                      >{#if markedIds.has(tx.id) && !tx.tags.includes(deletionTag)}<span
                          class="badge badge-sm m-0.5">{deletionTag}</span
                        >{/if}{#each tx.tags as tag}<span class="badge badge-sm m-0.5">{tag}</span
                        >{:else}<span class="text-base-content/70">No tags</span>{/each}</td
                    >
                    <td
                      >{#if link}<a
                          class="link whitespace-nowrap"
                          href={link}
                          target="_blank"
                          rel="external noopener noreferrer">Open #{tx.id}</a
                        >{:else}#{tx.id}{/if}</td
                    >
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        </article>
      {/each}
      {#if result.total > 0}
        <div class="flex flex-wrap items-center justify-end gap-3">
          <span class="toolkit-data text-xs"
            >{result.offset + 1}–{Math.min(result.offset + result.items.length, result.total)} of {result.total}</span
          >
          <button
            class="btn btn-sm"
            disabled={busy || marking || result.offset === 0}
            on:click={() => search(Math.max(0, result!.offset - 25))}>Previous</button
          >
          <button
            class="btn btn-sm"
            disabled={busy || marking || result.offset + result.limit >= result.total}
            on:click={() => search(result!.offset + 25)}>Next</button
          >
        </div>
      {/if}
    </section>
  {:else if options && !busy && !error}
    <p class="text-base-content/70 text-sm">Select an account and find candidates to compare.</p>
  {/if}
</div>

<dialog bind:this={reviewDialog} class="modal" aria-labelledby="deletion-review-title">
  <div class="modal-box max-w-3xl">
    <h3 id="deletion-review-title" class="text-lg font-semibold">Review deletion markers</h3>
    <p class="mt-2 text-sm">
      Add <code>{deletionTag}</code> to {selectedIds.size} transaction(s). No transactions will be deleted.
      Filter by this tag in Firefly to review and delete them later.
    </p>
    <ul class="mt-4 max-h-80 space-y-2 overflow-y-auto text-sm" aria-label="Transactions to mark">
      {#each pendingTransactions(result, selectedIds) as tx (tx.id)}
        <li class="border-base-300 rounded border p-2">
          <span class="toolkit-data">#{tx.id} · {tx.date} · {tx.amount} {tx.currency_code}</span>
          <p class="wrap-anywhere">{tx.description}</p>
          <p class="text-base-content/70 text-xs">Existing tags: {tx.tags.join(', ') || 'None'}</p>
        </li>
      {/each}
    </ul>
    <div class="modal-action">
      <button class="btn btn-sm" on:click={() => reviewDialog.close()}>Cancel</button>
      <button
        class="btn btn-sm btn-primary"
        disabled={marking || selectedIds.size === 0}
        on:click={markSelected}>Confirm marking ({selectedIds.size})</button
      >
    </div>
  </div>
  <form method="dialog" class="modal-backdrop"><button>Close review</button></form>
</dialog>
