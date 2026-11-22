<script lang="ts">
  import { onMount } from 'svelte';
  import { resolve } from '$app/paths';
  import { Icon } from '@steeze-ui/svelte-icon';
  import * as icons from '@steeze-ui/heroicons';
  import { getFireflyStatus, getTransactionSnapshotStatus } from '#lib/api/system.js';
  import { getMetricsStatus as getTxMetrics } from '#lib/api/tx.js';
  import { getMetricsStatus as getBlikMetrics } from '#lib/api/blik.js';
  import { getMetricsStatus as getAllegroMetrics } from '#lib/api/allegro.js';
  import type { components } from '#lib/api/schema.js';

  type SourceId = 'tx' | 'blik' | 'allegro';
  type Metric = {
    status: components['schemas']['JobStatus'] | null;
    count: number | null;
    timestamp: string | null;
    checking: boolean;
    failed: boolean;
    months: Record<string, number> | null;
  };
  const emptyMetric = (): Metric => ({
    status: null,
    count: null,
    timestamp: null,
    checking: true,
    failed: false,
    months: null
  });
  let metrics = $state<Record<SourceId, Metric>>({
    tx: emptyMetric(),
    blik: emptyMetric(),
    allegro: emptyMetric()
  });
  let firefly = $state<components['schemas']['FireflyStatusResponse'] | null>(null);
  let checkingFirefly = $state(true);
  let snapshot = $state<components['schemas']['TransactionSnapshotStatusResponse'] | null>(null);
  let checkingSnapshot = $state(true);
  let active = false;
  let snapshotRequest = 0;
  let fireflyRequest = 0;
  const requests: Record<SourceId, number> = { tx: 0, blik: 0, allegro: 0 };
  const timers: Partial<Record<SourceId, ReturnType<typeof setTimeout>>> = {};
  const checking = $derived(
    checkingFirefly || checkingSnapshot || Object.values(metrics).some((metric) => metric.checking)
  );
  const oldestMonth = $derived.by(() => {
    if (metrics.tx.count === null || metrics.tx.count === 0) return null;
    return (
      Object.entries(metrics.tx.months ?? {})
        .filter(
          ([month, count]) =>
            /^[1-9]\d{3}-(0[1-9]|1[0-2])$/.test(month) && Number.isSafeInteger(count) && count > 0
        )
        .map(([month]) => month)
        .sort()[0] ?? null
    );
  });
  const screeningQuery = $derived<'' | `?year=${number}&month=${number}`>(
    oldestMonth
      ? `?year=${Number(oldestMonth.slice(0, 4))}&month=${Number(oldestMonth.slice(5))}`
      : ''
  );

  const sources = [
    {
      id: 'tx',
      title: 'Categorization',
      description: 'Uncategorized transactions eligible for screening.',
      label: 'ready to categorize',
      action: 'Categorize',
      href: 'tx/categorize',
      stats: 'tx/stats',
      icon: icons.DocumentMagnifyingGlass
    },
    {
      id: 'blik',
      title: 'BLIK',
      description: 'Firefly transactions waiting for BLIK processing.',
      label: 'unprocessed BLIK transactions',
      action: 'Upload file',
      href: 'blik/upload',
      stats: 'blik/stats',
      icon: icons.InboxArrowDown
    },
    {
      id: 'allegro',
      title: 'Allegro',
      description: 'Firefly transactions waiting for Allegro processing.',
      label: 'unprocessed Allegro transactions',
      action: 'Open accounts',
      href: 'allegro/accounts',
      stats: 'allegro/stats',
      icon: icons.BuildingStorefront
    }
  ] as const;

  function validTimestamp(value: string | null | undefined): value is string {
    return (
      typeof value === 'string' && value.trim().length > 0 && Number.isFinite(Date.parse(value))
    );
  }
  function formatTimestamp(value: string) {
    return new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' }).format(
      new Date(value)
    );
  }
  function countOrNull(value: unknown) {
    return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
  }

  async function readMetric(id: SourceId) {
    if (id === 'tx') {
      const response = await getTxMetrics();
      return {
        status: response.status,
        count: response.result?.categorizable,
        timestamp: response.result?.time_stamp,
        months: response.result?.categorizable_by_month ?? null
      };
    }
    if (id === 'blik') {
      const response = await getBlikMetrics();
      return {
        status: response.status,
        count: response.result?.not_processed_transactions,
        timestamp: response.result?.time_stamp,
        months: null
      };
    }
    const response = await getAllegroMetrics();
    return {
      status: response.status,
      count: response.result?.not_processed__allegro_transactions,
      timestamp: response.result?.time_stamp,
      months: null
    };
  }

  async function refreshSnapshot() {
    const request = ++snapshotRequest;
    checkingSnapshot = true;
    snapshot = null;
    try {
      const result = await getTransactionSnapshotStatus();
      if (!active || request !== snapshotRequest) return;
      if (typeof result?.has_snapshot === 'boolean' && typeof result?.is_stale === 'boolean')
        snapshot = result;
    } catch {
      // Unavailable freshness must not affect workflow links or valid source counts.
    } finally {
      if (active && request === snapshotRequest) checkingSnapshot = false;
    }
  }

  async function refreshFirefly() {
    const request = ++fireflyRequest;
    checkingFirefly = true;
    firefly = null;
    try {
      const result = await getFireflyStatus();
      if (
        active &&
        request === fireflyRequest &&
        ['ok', 'error', 'not_configured'].includes(result?.status)
      )
        firefly = result;
    } catch {
      // Connection failure is shown independently of stored transaction data.
    } finally {
      if (active && request === fireflyRequest) checkingFirefly = false;
    }
  }

  async function refreshMetric(id: SourceId, attempt = 0) {
    clearTimeout(timers[id]);
    const request = ++requests[id];
    const wasCalculating = metrics[id].status === 'running' || metrics[id].status === 'pending';
    metrics[id] = attempt === 0 ? emptyMetric() : { ...metrics[id], checking: true };
    try {
      const result = await readMetric(id);
      if (!active || request !== requests[id]) return;
      const done = result.status === 'done';
      const failed = !['pending', 'running', 'done'].includes(result.status);
      metrics[id] = {
        status: result.status,
        count: done ? countOrNull(result.count) : null,
        timestamp: validTimestamp(result.timestamp) ? result.timestamp : null,
        months: done ? result.months : null,
        checking: false,
        failed
      };
      if ((result.status === 'pending' || result.status === 'running') && attempt < 15) {
        timers[id] = setTimeout(() => {
          if (active) void refreshMetric(id, attempt + 1);
        }, 2000);
      } else if (done && wasCalculating) {
        void refreshSnapshot();
      }
    } catch {
      if (active && request === requests[id])
        metrics[id] = { ...emptyMetric(), checking: false, failed: true };
    }
  }

  function refreshOverview() {
    void refreshFirefly();
    void refreshSnapshot();
    for (const source of sources) void refreshMetric(source.id);
  }

  onMount(() => {
    active = true;
    refreshOverview();
    return () => {
      active = false;
      for (const timer of Object.values(timers)) clearTimeout(timer);
    };
  });
</script>

<svelte:head><title>Task overview — Firefly Toolkit</title></svelte:head>

<div class="mx-auto flex w-full max-w-7xl min-w-0 flex-col gap-3">
  <div class="flex flex-wrap items-center justify-between gap-2">
    <h2 class="text-base font-semibold">Task overview</h2>
    <button class="btn btn-outline btn-sm" disabled={checking} onclick={refreshOverview}>
      <Icon src={icons.ArrowPath} class="h-4 w-4" /> Refresh overview
    </button>
  </div>

  <section
    aria-label="Connection and data"
    class="border-base-300 bg-base-100 rounded-box grid min-w-0 gap-3 border px-3 py-3 text-xs sm:grid-cols-2 sm:px-4"
  >
    <div class="min-w-0" role="status" aria-label="Firefly connection">
      <h3 class="mb-1 font-semibold">Firefly III</h3>
      {#if checkingFirefly}
        <p class="text-base-content/70">Checking connection…</p>
      {:else if firefly?.status === 'ok'}
        <p class="text-success font-medium">Connected</p>
        <p class="text-base-content/70 mt-1 wrap-anywhere">
          Version {firefly.version ?? 'unknown'} · API {firefly.api_version ?? 'unknown'}
        </p>
      {:else if firefly?.status === 'not_configured'}
        <p>Connection not configured</p>
      {:else if firefly?.status === 'error'}
        <p class="text-error">Connection unavailable</p>
      {:else}
        <p class="text-base-content/70">Connection information unavailable</p>
      {/if}
      {#if !checkingFirefly && validTimestamp(firefly?.timestamp)}
        <p class="text-base-content/60 mt-1">
          Checked <time datetime={firefly.timestamp}>{formatTimestamp(firefly.timestamp)}</time>
        </p>
      {/if}
    </div>
    <div class="min-w-0" role="status" aria-label="Transaction data freshness">
      <h3 class="mb-1 font-semibold">Transaction data</h3>
      {#if checkingSnapshot}
        <p class="text-base-content/70">Checking data freshness…</p>
      {:else if snapshot?.has_snapshot === false}
        <p>No stored snapshot</p>
      {:else if snapshot?.has_snapshot && validTimestamp(snapshot.snapshot_fetched_at)}
        <p class={snapshot.is_stale ? 'text-warning' : ''}>
          {snapshot.is_stale ? 'Stale snapshot at last check' : 'Fresh snapshot at last check'}
        </p>
        <p class="text-base-content/70 mt-1">
          Retrieved <time datetime={snapshot.snapshot_fetched_at}>
            {formatTimestamp(snapshot.snapshot_fetched_at)}
          </time>
        </p>
      {:else}
        <p class="text-base-content/70">Data freshness unavailable</p>
      {/if}
    </div>
  </section>

  <section
    aria-label="Workflows"
    class="border-base-300 bg-base-100 divide-base-300 rounded-box min-w-0 divide-y border"
  >
    {#each sources as source}
      {@const metric = metrics[source.id]}
      <article
        aria-label={source.title}
        class="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-2 p-3 sm:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_auto] sm:items-center sm:p-4"
      >
        <div class="min-w-0">
          <h3 class="flex items-center gap-2 text-sm font-semibold">
            <Icon src={source.icon} class="h-4 w-4 shrink-0" />
            {source.title}
          </h3>
          <p class="text-base-content/70 mt-1 text-xs wrap-anywhere">{source.description}</p>
        </div>
        <div
          class="col-span-2 min-w-0 text-xs sm:col-span-1 sm:col-start-2 sm:row-start-1"
          role="status"
          aria-label={`${source.title} count`}
        >
          {#if metric.checking}
            <p class="text-base-content/70">Loading count…</p>
          {:else if metric.status === 'pending' || metric.status === 'running'}
            <p class="text-base-content/70">Calculating count…</p>
          {:else if metric.failed || metric.count === null}
            <p class="text-base-content/70">Count unavailable</p>
          {:else}
            <a class="link inline-block font-medium wrap-anywhere" href={resolve(source.stats)}>
              <span class="toolkit-data">{metric.count}</span>
              {source.label}
            </a>
            {#if metric.count === 0}<p class="text-base-content/70 mt-1">
                No work in this snapshot.
              </p>{/if}
          {/if}
          {#if !metric.checking && metric.count === null}
            <a class="link mt-1 inline-block" href={resolve(source.stats)}>Open statistics</a>
          {/if}
          {#if validTimestamp(metric.timestamp)}
            <p class="text-base-content/60 mt-1">
              Snapshot <time datetime={metric.timestamp}>{formatTimestamp(metric.timestamp)}</time>
            </p>
          {:else if !metric.checking && metric.status === 'done'}
            <p class="text-base-content/60 mt-1">Snapshot time unavailable</p>
          {/if}
        </div>
        <div class="col-start-2 row-start-1 text-right sm:col-start-3">
          <a
            class="btn btn-outline btn-sm"
            href={source.id === 'tx'
              ? resolve(`tx/categorize${screeningQuery}`)
              : resolve(source.href)}
          >
            {source.action}
          </a>
          {#if source.id === 'tx' && oldestMonth}<p class="text-base-content/60 mt-1 text-xs">
              Oldest month: <span class="toolkit-data">{oldestMonth}</span>
            </p>{/if}
        </div>
      </article>
    {/each}
    <article
      aria-label="VeloBank import"
      class="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-3 p-3 sm:items-center sm:p-4"
    >
      <div class="min-w-0">
        <h3 class="flex items-center gap-2 text-sm font-semibold">
          <Icon src={icons.DocumentArrowUp} class="h-4 w-4 shrink-0" /> VeloBank import
        </h3>
        <p class="text-base-content/70 mt-1 text-xs">
          Upload a bank PDF and export CSV for Firefly.
        </p>
        <p class="text-base-content/60 mt-1 text-xs">File-based workflow · no pending count</p>
      </div>
      <a class="btn btn-outline btn-sm" href={resolve('tools/velobank')}>Import PDF</a>
    </article>
  </section>

  <details class="text-base-content/70 px-1 text-xs">
    <summary class="w-fit cursor-pointer font-medium">About these counts</summary>
    <p class="mt-2">
      Each count uses its own statistics snapshot and definition. Counts are not added together.
    </p>
    <dl class="mt-2 space-y-2">
      <div>
        <dt class="text-base-content font-medium">Categorization</dt>
        <dd>
          Uncategorized single-part transactions eligible for screening, excluding the configured
          BLIK description, Action Required and unfinished Allegro processing. The action opens the
          oldest pending month when available.
        </dd>
      </div>
      <div>
        <dt class="text-base-content font-medium">BLIK</dt>
        <dd>
          Uncategorized single-part Firefly transactions matching the configured BLIK description
          exactly, without the blik_done tag.
        </dd>
      </div>
      <div>
        <dt class="text-base-content font-medium">Allegro</dt>
        <dd>
          Single-part Firefly transactions matching the configured Allegro description, without the
          allegro_done tag. This is a transaction count, not a count of marketplace payments.
        </dd>
      </div>
    </dl>
  </details>
</div>
