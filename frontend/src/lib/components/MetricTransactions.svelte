<script lang="ts">
  import { onDestroy } from 'svelte';
  import { ApiError } from '#lib/api/errors.js';
  import {
    getMetricTransactions,
    type MetricTransactions,
    type MetricSort
  } from '#lib/api/metric-transactions.js';

  export let provider: 'allegro' | 'blik';
  export let timestamp: string;
  export let month: string | null = null;
  export let reloadStatistics: () => Promise<void>;

  let result: MetricTransactions | null = null;
  let offset = 0;
  let sort: MetricSort = 'date_desc';
  let loading = false;
  let error: string | null = null;
  let changed = false;
  let requestId = 0;

  function resetOffset(..._selection: unknown[]) {
    return 0;
  }
  $: offset = resetOffset(month, timestamp, sort);
  $: load(provider, timestamp, month, offset, sort);
  onDestroy(() => {
    requestId += 1;
  });

  async function load(
    feature: 'allegro' | 'blik',
    version: string,
    selectedMonth: string | null,
    pageOffset: number,
    order: MetricSort
  ) {
    const id = ++requestId;
    loading = true;
    error = null;
    changed = false;
    result = null;
    try {
      const next = await getMetricTransactions(feature, version, selectedMonth, pageOffset, order);
      if (id === requestId) result = next;
    } catch (e) {
      if (id !== requestId) return;
      changed = e instanceof ApiError && e.status === 409;
      error = changed
        ? 'Statistics changed. Reload the chart and transactions to continue.'
        : e instanceof Error
          ? e.message
          : 'Failed to load transactions';
    } finally {
      if (id === requestId) loading = false;
    }
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

<section class="card bg-base-100 mt-6" aria-label="Unprocessed transactions">
  <div class="card-body p-4">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h3 class="text-xl font-semibold">
        Unprocessed transactions {month ? `— ${month}` : '— all months'}
      </h3>
      <div class="flex flex-wrap items-center gap-2">
        {#if month}<button class="btn btn-sm" on:click={() => (month = null)}>All months</button
          >{/if}
        <label class="flex items-center gap-2"
          >Sort
          <select class="select select-sm" bind:value={sort}>
            <option value="date_desc">Newest first</option><option value="date_asc"
              >Oldest first</option
            >
            <option value="amount_desc">Amount: high to low</option><option value="amount_asc"
              >Amount: low to high</option
            >
          </select>
        </label>
      </div>
    </div>
    {#if loading}
      <p role="status">Loading transactions…</p>
    {:else if error}
      <div class="alert alert-warning" role="alert">
        <span>{error}</span>
        <button
          class="btn btn-sm"
          on:click={() =>
            changed ? reloadStatistics() : load(provider, timestamp, month, offset, sort)}
          >{changed ? 'Reload statistics and transactions' : 'Retry'}</button
        >
      </div>
    {:else if result}
      <p aria-live="polite">Total: {result.total}</p>
      {#if result.total === 0}<p>No unprocessed transactions for this selection.</p>
      {:else}
        <div class="overflow-x-auto">
          <table class="table-sm table">
            <thead
              ><tr
                ><th>Date</th><th>Description</th><th>Amount</th><th>Source account</th><th
                  >Destination account</th
                ><th>Tags</th><th>Firefly</th></tr
              ></thead
            >
            <tbody
              >{#each result.items as tx (tx.id)}
                {@const link = fireflyLink(result.firefly_url, tx.id)}
                <tr
                  ><td class="toolkit-data whitespace-nowrap">{tx.date}</td><td>{tx.description}</td
                  ><td class="toolkit-data whitespace-nowrap">{tx.amount} {tx.currency_code}</td><td
                    >{tx.source_account?.name ?? '—'}</td
                  ><td>{tx.destination_account?.name ?? '—'}</td><td
                    >{#each tx.tags as tag}<span class="badge badge-sm mr-1">{tag}</span>{/each}</td
                  ><td
                    >{#if link}<a
                        class="link"
                        href={link}
                        target="_blank"
                        rel="external noopener noreferrer">Open #{tx.id}</a
                      >{:else}#{tx.id}{/if}</td
                  ></tr
                >
              {/each}</tbody
            >
          </table>
        </div>
        <div class="flex items-center justify-end gap-3">
          <span
            >{offset + 1}–{Math.min(offset + result.items.length, result.total)} of {result.total}</span
          >
          <button
            class="btn btn-sm"
            disabled={offset === 0}
            on:click={() => (offset = Math.max(0, offset - 25))}>Previous</button
          >
          <button
            class="btn btn-sm"
            disabled={offset + result.limit >= result.total}
            on:click={() => (offset += 25)}>Next</button
          >
        </div>
      {/if}
    {/if}
  </div>
</section>
