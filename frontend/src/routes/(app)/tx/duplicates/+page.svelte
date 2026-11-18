<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import WorkflowHeader from '#lib/components/WorkflowHeader.svelte';
  import { ApiError } from '#lib/api/errors.js';
  import {
    getDuplicateOptions,
    refreshDuplicateOptions,
    getDuplicateResults,
    type DuplicateOptions,
    type DuplicateResults,
    type DuplicateQuery
  } from '#lib/api/duplicates.js';

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
    busy = true;
    error = null;
    changed = false;
    result = null;
    submitted = null;
    try {
      const next = await (forceRefresh ? refreshDuplicateOptions() : getDuplicateOptions());
      if (id !== requestId) return;
      options = next;
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
    <button slot="actions" class="btn btn-sm" disabled={busy} on:click={() => loadOptions(true)}
      >Reload transactions</button
    >
  </WorkflowHeader>

  {#if options}
    <form class="card bg-base-100 p-4" on:submit={submit}>
      <fieldset
        disabled={busy || options.accounts.length === 0}
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
        disabled={busy}
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
        These are review candidates, including possible legitimate repeated payments. No
        transactions are modified.
      </p>
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
          </div>
          <div class="overflow-x-auto">
            <table class="table-sm table">
              <caption class="sr-only">Transactions in candidate pair {pair.id}</caption>
              <thead
                ><tr
                  ><th>Date</th><th>Description</th><th>Type</th><th>Source account</th><th
                    >Destination account</th
                  ><th>Tags</th><th>Firefly</th></tr
                ></thead
              >
              <tbody>
                {#each [pair.first, pair.second] as tx (tx.id)}
                  {@const link = fireflyLink(result.firefly_url, tx.id)}
                  <tr>
                    <td class="toolkit-data whitespace-nowrap">{tx.date}</td>
                    <td class="min-w-40 wrap-anywhere whitespace-normal">{tx.description}</td>
                    <td>{tx.type}</td><td>{tx.source_account?.name ?? '—'}</td><td
                      >{tx.destination_account?.name ?? '—'}</td
                    >
                    <td
                      >{#each tx.tags as tag}<span class="badge badge-sm m-0.5">{tag}</span
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
            disabled={busy || result.offset === 0}
            on:click={() => search(Math.max(0, result!.offset - 25))}>Previous</button
          >
          <button
            class="btn btn-sm"
            disabled={busy || result.offset + result.limit >= result.total}
            on:click={() => search(result!.offset + 25)}>Next</button
          >
        </div>
      {/if}
    </section>
  {:else if options && !busy && !error}
    <p class="text-base-content/70 text-sm">Select an account and find candidates to compare.</p>
  {/if}
</div>
