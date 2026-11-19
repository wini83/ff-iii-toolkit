<script lang="ts">
  import MetricTransactions from '#lib/components/MetricTransactions.svelte';
  let showTransactions = false;
  let selectedMonth: string | null = null;

  function showMonth(month: string | null) {
    selectedMonth = month;
    showTransactions = true;
  }

  import { onMount } from 'svelte';

  import { blik } from '#lib/api/blik.js';
  import ProviderAmounts from '#lib/components/metrics/ProviderAmounts.svelte';
  import MetricCard from '#lib/components/metrics/MetricCard.svelte';
  import MetricsToolbar from '#lib/components/metrics/MetricsToolbar.svelte';
  import MonthlyChart from '#lib/components/metrics/MonthlyChart.svelte';
  import type { components } from '#lib/api/schema.js';

  type BlikMetricsStatusResponse = components['schemas']['BlikMetricsStatusResponse'];
  type BlikMetricsResultResponse = components['schemas']['BlikMetricsResultResponse'];
  type JobStatus = components['schemas']['JobStatus'];

  const POLL_INTERVAL_MS = 2_000;
  const MAX_POLL_ATTEMPTS = 60;

  let statusData: BlikMetricsStatusResponse | null = null;
  let data: BlikMetricsResultResponse | null = null;
  let metricMode: 'count' | 'amount' = 'count';
  let chartCurrency = '';
  $: currencies = Object.keys(data?.amount_metrics?.eligible_amount ?? {}).sort();
  $: if (!currencies.includes(chartCurrency))
    chartCurrency = currencies.includes('PLN') ? 'PLN' : (currencies[0] ?? '');
  let loading = true;
  let networkError: string | null = null;

  let notProcessed = {
    labels: [] as string[],
    values: [] as number[]
  };

  function toChartData(obj: Record<string, number>) {
    return {
      labels: Object.keys(obj).sort(),
      values: Object.keys(obj)
        .sort()
        .map((month) => obj[month])
    };
  }

  function wait(ms: number) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function isBusy(status: JobStatus | undefined) {
    return status === 'pending' || status === 'running';
  }

  function isFailed(status: JobStatus | undefined) {
    return status === 'failed';
  }

  function isDone(status: JobStatus | undefined) {
    return status === 'done';
  }

  function hasNoData(status: BlikMetricsStatusResponse | null) {
    return isDone(status?.status) && status?.result === null;
  }

  function hasTotalTransactions(
    result: BlikMetricsResultResponse
  ): result is BlikMetricsResultResponse & { total_transactions: number } {
    return (
      'total_transactions' in result &&
      typeof (result as BlikMetricsResultResponse & { total_transactions?: unknown })
        .total_transactions === 'number'
    );
  }

  function getTotalTransactions(result: BlikMetricsResultResponse) {
    return hasTotalTransactions(result)
      ? result.total_transactions
      : result.single_part_transactions;
  }

  function getNonEmptyString(value: unknown): string | null {
    if (typeof value !== 'string') return null;
    const normalized = value.trim();
    return normalized.length ? normalized : null;
  }

  function extractErrorMessage(error: unknown, fallback: string) {
    const normalizedFallback = getNonEmptyString(fallback) ?? 'Unexpected error';
    const rawError = getNonEmptyString(error);
    if (rawError) return rawError;

    if (error && typeof error === 'object') {
      const err = error as { detail?: unknown; error?: unknown; message?: unknown };

      const message = getNonEmptyString(err.message);
      if (message) return message;

      const errText = getNonEmptyString(err.error);
      if (errText) return errText;

      if (Array.isArray(err.detail)) {
        const joined = err.detail
          .map((item) => {
            if (item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string') {
              return getNonEmptyString(item.msg);
            }
            return null;
          })
          .filter((item): item is string => item !== null)
          .join('; ');

        if (joined) return joined;
      }

      const detail = getNonEmptyString(err.detail);
      if (detail) return detail;
    }

    return normalizedFallback;
  }

  function emitToast(type: 'info' | 'success' | 'error', msg: string) {
    const safeMsg =
      getNonEmptyString(msg) ??
      (type === 'error' ? 'Failed to load statistics' : type === 'success' ? 'Success' : 'Info');

    window.dispatchEvent(
      new CustomEvent('toast', {
        detail: { type, msg: safeMsg }
      })
    );
  }

  async function pollUntilDone(initialStatus: BlikMetricsStatusResponse) {
    let current = initialStatus;

    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS && isBusy(current.status); attempt += 1) {
      await wait(POLL_INTERVAL_MS);
      current = await blik.getMetricsStatus();
      statusData = current;
    }

    return current;
  }

  function updateUiFromStatus(status: BlikMetricsStatusResponse | null) {
    const result = isDone(status?.status) ? (status?.result ?? null) : null;
    data = result;

    if (!result) {
      notProcessed = { labels: [], values: [] };
      return;
    }

    notProcessed = toChartData(result.not_processed_by_month);
  }

  onMount(() => {
    loadStats(false);
  });

  async function loadStats(force = false) {
    loading = true;
    networkError = null;

    if (force) {
      emitToast('info', 'Statistics generation has started');
    }

    try {
      let nextStatus = force ? await blik.refreshMetricsStatus() : await blik.getMetricsStatus();
      statusData = nextStatus;

      if (isBusy(nextStatus.status)) {
        nextStatus = await pollUntilDone(nextStatus);
        statusData = nextStatus;
      }

      if (isBusy(nextStatus.status))
        throw new Error('Statistics are still generating. Try Refresh again.');

      if (isFailed(nextStatus.status)) {
        const message = nextStatus.error ?? 'Statistics generation failed';
        networkError = message;
        emitToast('error', message);
        updateUiFromStatus(null);
        return;
      }

      updateUiFromStatus(nextStatus);

      if (hasNoData(nextStatus)) {
        emitToast('info', 'Brak danych');
      }

      if (force && isDone(nextStatus.status)) {
        emitToast('success', 'Statistics generation completed');
      }
    } catch (error: unknown) {
      networkError = extractErrorMessage(error, 'Failed to load statistics');
      emitToast('error', networkError);
      statusData = null;
      updateUiFromStatus(null);
    } finally {
      loading = false;
    }
  }
</script>

<svelte:head><title>BLIK statistics — Firefly Toolkit</title></svelte:head>

<div class="mx-auto flex w-full max-w-7xl min-w-0 flex-col gap-4">
  <MetricsToolbar
    title="BLIK statistics"
    timestamp={data?.time_stamp}
    fetchSeconds={data?.fetch_seconds}
    busy={loading || isBusy(statusData?.status)}
    allowAmounts
    mode={metricMode}
    {currencies}
    currency={chartCurrency}
    onMode={(mode) => (metricMode = mode)}
    onCurrency={(currency) => (chartCurrency = currency)}
    onRefresh={() => loadStats(true)}
  />
  {#if networkError}<div class="alert alert-error" role="alert">{networkError}</div>{/if}
  {#if loading || isBusy(statusData?.status)}
    <div
      class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
      role="status"
      aria-label="Loading statistics"
    >
      {#each [1, 2, 3, 4] as index (index)}<div class="skeleton h-28"></div>{/each}
    </div>
  {:else if data}
    {#if metricMode === 'amount'}
      <ProviderAmounts
        data={data.amount_metrics}
        currency={chartCurrency}
        title="Not processed amount by month"
        onMonth={showMonth}
      />
    {:else}
      <div class="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Total transactions"
          count={getTotalTransactions(data)}
          note={`Single-part: ${data.single_part_transactions}`}
        />
        <MetricCard label="Uncategorized" count={data.uncategorized_transactions} />
        <MetricCard
          label="Not processed (TAG)"
          count={data.not_processed_transactions}
          note={`By description: ${data.filtered_by_description_exact}`}
        />
      </div>
      <MonthlyChart
        title="Not processed by month"
        labels={notProcessed.labels}
        values={notProcessed.values}
        onMonth={showMonth}
      />
    {/if}
  {:else if !networkError}
    <p class="border-base-300 rounded-sm border p-4 text-sm">
      No statistics data yet. Use Refresh to generate a snapshot.
    </p>
  {/if}
  {#if data && !loading && !isBusy(statusData?.status)}
    <div>
      <button class="btn btn-outline btn-sm" on:click={() => showMonth(null)}
        >Show unprocessed transactions</button
      >
    </div>
    {#if showTransactions}{#key data.time_stamp}
        <MetricTransactions
          provider="blik"
          timestamp={data.time_stamp}
          bind:month={selectedMonth}
          reloadStatistics={() => loadStats(false)}
        />
      {/key}{/if}
  {/if}
</div>
