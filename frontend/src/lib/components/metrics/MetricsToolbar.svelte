<script lang="ts">
  export let title: string;
  export let timestamp: string | undefined = undefined;
  export let fetchSeconds: number | undefined = undefined;
  export let busy = false;
  export let mode: 'count' | 'amount' = 'count';
  export let allowAmounts = false;
  export let currencies: string[] = [];
  export let currency = '';
  export let onMode: (mode: 'count' | 'amount') => void = () => {};
  export let onCurrency: (currency: string) => void = () => {};
  export let onRefresh: () => void;

  function dateLabel(value: string) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString('pl-PL');
  }
</script>

<header
  class="border-base-300 flex min-w-0 flex-wrap items-center justify-between gap-3 border-b pb-3"
>
  <div class="min-w-0">
    <h2 class="text-lg font-semibold">{title}</h2>
    <details class="text-base-content/70 mt-1 text-xs">
      <summary class="cursor-pointer"
        >Data: {timestamp ? dateLabel(timestamp) : 'unavailable'}</summary
      >
      <p class="mt-1">
        Generation time: {typeof fetchSeconds === 'number'
          ? `${fetchSeconds.toFixed(2)} s`
          : 'unavailable'}
      </p>
    </details>
  </div>
  <div class="flex min-w-0 flex-wrap items-center gap-2">
    {#if allowAmounts}
      <div class="join" role="group" aria-label="Metric mode">
        <button
          class="btn btn-sm join-item"
          class:btn-active={mode === 'count'}
          aria-pressed={mode === 'count'}
          on:click={() => onMode('count')}>Count</button
        >
        <button
          class="btn btn-sm join-item"
          class:btn-active={mode === 'amount'}
          aria-pressed={mode === 'amount'}
          on:click={() => onMode('amount')}>Amount</button
        >
      </div>
      {#if mode === 'amount' && currencies.length}
        <label class="flex items-center gap-2 text-sm"
          >Chart currency
          <select
            class="select select-sm w-auto"
            aria-label="Chart currency"
            value={currency}
            on:change={(event) => onCurrency(event.currentTarget.value)}
          >
            {#each currencies as code}<option value={code}>{code}</option>{/each}
          </select>
        </label>
      {/if}
    {/if}
    <button class="btn btn-sm" disabled={busy} on:click={onRefresh}
      >{busy ? 'Refreshing…' : 'Refresh'}</button
    >
  </div>
</header>
