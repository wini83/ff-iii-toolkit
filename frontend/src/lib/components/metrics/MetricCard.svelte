<script lang="ts">
  export let label: string;
  export let count: number;
  export let mode: 'count' | 'amount' = 'count';
  export let amounts: Record<string, string> = {};
  export let note = '';

  function amountLabel(value: string) {
    const number = Number(value);
    if (!Number.isFinite(number)) return 'Unavailable';
    return new Intl.NumberFormat('pl-PL', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(number);
  }
</script>

<section class="card bg-base-100 min-w-0 p-4" aria-label={label} data-metric-card>
  <h3 class="text-base-content/70 text-sm">{label}</h3>
  {#if mode === 'count'}
    <p class="toolkit-data mt-2 text-2xl font-semibold [overflow-wrap:anywhere]">
      {count.toLocaleString('pl-PL')}
    </p>
  {:else if Object.keys(amounts).length}
    <dl class="mt-2 space-y-2 text-sm">
      {#each Object.entries(amounts).sort(([a], [b]) => a.localeCompare(b)) as [currency, amount]}
        <div class="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <dt class="text-base-content/70 font-mono">{currency}</dt>
          <dd class="toolkit-data min-w-0 text-lg font-semibold [overflow-wrap:anywhere]">
            {amountLabel(amount)}
          </dd>
        </div>
      {/each}
    </dl>
  {:else}
    <p class="text-base-content/70 mt-2 text-sm">No recorded amounts</p>
  {/if}
  {#if note}<p class="text-base-content/70 mt-2 text-sm [overflow-wrap:anywhere]">{note}</p>{/if}
  <slot />
</section>
