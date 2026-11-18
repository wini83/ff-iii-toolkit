<script lang="ts">
  import MetricCard from './MetricCard.svelte';
  import MonthlyChart from './MonthlyChart.svelte';
  import type { components } from '#lib/api/schema.js';

  export let data: components['schemas']['ProviderAmountMetricsResponse'] | null | undefined;
  export let currency = '';
  export let title: string;
  export let onMonth: (month: string) => void;

  $: labels = Object.keys(data?.not_processed_amount_by_month ?? {}).sort();
  $: values = labels.map((month) => {
    const value = data?.not_processed_amount_by_month[month]?.[currency];
    return value === undefined ? null : Number(value);
  });
</script>

{#if data}
  <p class="text-base-content/70 text-sm">
    Gross bank amounts: absolute values, including refunds. Currencies are kept separate.
  </p>
  <div class="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
    <MetricCard label="Eligible amount" count={0} mode="amount" amounts={data.eligible_amount} />
    <MetricCard label="Processed amount" count={0} mode="amount" amounts={data.processed_amount} />
    <MetricCard
      label="Not processed amount"
      count={0}
      mode="amount"
      amounts={data.not_processed_amount}
    />
    <section class="card bg-base-100 min-w-0 p-4" aria-label="Amount processing coverage">
      <h3 class="text-base-content/70 text-sm">Amount processing coverage</h3>
      <dl class="mt-2 space-y-2 text-sm">
        {#each Object.entries(data.processing_coverage) as [code, coverage]}
          <div class="flex flex-wrap justify-between gap-2">
            <dt class="font-mono">{code}</dt>
            <dd class="toolkit-data font-semibold">
              {coverage === null
                ? 'n/a'
                : `${Number(coverage).toLocaleString('pl-PL', { maximumFractionDigits: 2 })}%`}
            </dd>
          </div>
        {:else}<p>No recorded amounts</p>{/each}
      </dl>
    </section>
  </div>
  <MonthlyChart {title} {labels} {values} {currency} {onMonth} />
{:else}
  <p class="border-base-300 rounded-sm border p-4 text-sm">
    Amount statistics are unavailable for this snapshot. Use Refresh to generate them.
  </p>
{/if}
