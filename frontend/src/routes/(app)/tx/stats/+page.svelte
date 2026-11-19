<script lang="ts">
  import { onMount } from 'svelte';

  import { getMetricsStatus, refreshMetricsStatus } from '#lib/api/tx.js';
  import MetricCard from '#lib/components/metrics/MetricCard.svelte';
  import MetricsToolbar from '#lib/components/metrics/MetricsToolbar.svelte';
  import MonthlyChart from '#lib/components/metrics/MonthlyChart.svelte';
  import type { components } from '#lib/api/schema.js';

  type TxMetricsStatusResponse = components['schemas']['TxMetricsStatusResponse'];
  type TxMetricsResultResponse = components['schemas']['TxMetricsResultResponse'];
  type JobStatus = components['schemas']['JobStatus'];

  const POLL_INTERVAL_MS = 2_000;
  const MAX_POLL_ATTEMPTS = 60;

  let statusData: TxMetricsStatusResponse | null = null;
  let data: TxMetricsResultResponse | null = null;
  let loading = true;
  let networkError: string | null = null;
  let metricMode: 'count' | 'amount' = 'count';

  let chartCurrency = '';
  let currencies: string[];
  $: currencies = data
    ? Array.from(
        new Set([
          ...Object.keys(data.single_part_amount ?? {}),
          ...Object.keys(data.uncategorized_amount ?? {}),
          ...Object.keys(data.action_req_amount ?? {}),
          ...Object.keys(data.categorizable_amount ?? {}),
          ...Object.values(data.categorizable_amount_by_month ?? {}).flatMap((amounts) =>
            Object.keys(amounts)
          )
        ])
      ).sort()
    : [];
  $: if (!currencies.includes(chartCurrency))
    chartCurrency = currencies.includes('PLN') ? 'PLN' : (currencies[0] ?? '');
  $: chartLabels = Object.keys(
    metricMode === 'count'
      ? (data?.categorizable_by_month ?? {})
      : (data?.categorizable_amount_by_month ?? {})
  ).sort();
  $: chartValues = chartLabels.map((month) =>
    metricMode === 'count'
      ? (data?.categorizable_by_month[month] ?? null)
      : data?.categorizable_amount_by_month[month]?.[chartCurrency] === undefined
        ? null
        : Number(data.categorizable_amount_by_month[month][chartCurrency])
  );

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

  function hasNoData(status: TxMetricsStatusResponse | null) {
    return isDone(status?.status) && status?.result === null;
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

  async function pollUntilDone(initialStatus: TxMetricsStatusResponse) {
    let current = initialStatus;

    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS && isBusy(current.status); attempt += 1) {
      await wait(POLL_INTERVAL_MS);
      current = await getMetricsStatus();
      statusData = current;
    }

    return current;
  }

  function updateUiFromStatus(status: TxMetricsStatusResponse | null) {
    data = isDone(status?.status) ? (status?.result ?? null) : null;
  }

  function setMetricMode(mode: 'count' | 'amount') {
    metricMode = mode;
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
      let nextStatus = force ? await refreshMetricsStatus() : await getMetricsStatus();
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

<svelte:head><title>Transaction statistics — Firefly Toolkit</title></svelte:head>

<div class="mx-auto flex w-full max-w-7xl min-w-0 flex-col gap-4">
  <MetricsToolbar
    title="Transaction statistics"
    timestamp={data?.time_stamp}
    fetchSeconds={data?.fetch_seconds}
    busy={loading || isBusy(statusData?.status)}
    onRefresh={() => loadStats(true)}
    allowAmounts
    mode={metricMode}
    {currencies}
    currency={chartCurrency}
    onMode={setMetricMode}
    onCurrency={(currency) => (chartCurrency = currency)}
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
    <div class="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        label="Single-part transactions"
        count={data.single_part_transactions}
        mode={metricMode}
        amounts={data.single_part_amount}
      />
      <MetricCard
        label="Uncategorized"
        count={data.uncategorized_transactions}
        mode={metricMode}
        amounts={data.uncategorized_amount}
      />
      <MetricCard
        label="Action required"
        count={data.action_req}
        mode={metricMode}
        amounts={data.action_req_amount}
      />
      <MetricCard
        label="Categorizable"
        count={data.categorizable}
        mode={metricMode}
        amounts={data.categorizable_amount}
      />
    </div>
    <details class="border-base-300 rounded-sm border p-3">
      <summary class="cursor-pointer text-sm">Processing exclusions</summary>
      <div class="mt-3 grid gap-3 sm:grid-cols-2">
        <MetricCard
          label="BLIK not OK"
          count={data.blik_not_ok}
          mode={metricMode}
          amounts={data.blik_not_ok_amount}
        />
        <MetricCard
          label="Allegro not OK"
          count={data.allegro_not_ok}
          mode={metricMode}
          amounts={data.allegro_not_ok_amount}
        />
      </div>
    </details>
    <MonthlyChart
      title="Categorizable by month"
      labels={chartLabels}
      values={chartValues}
      currency={metricMode === 'amount' ? chartCurrency : ''}
    />
  {:else if !networkError}
    <p class="border-base-300 rounded-sm border p-4 text-sm">
      No statistics data yet. Use Refresh to generate a snapshot.
    </p>
  {/if}
</div>
