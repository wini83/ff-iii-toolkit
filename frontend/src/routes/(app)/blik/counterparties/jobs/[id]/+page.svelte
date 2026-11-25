<script lang="ts">
  import { page } from '$app/state';
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { getCounterpartyJob, type CounterpartyJob } from '#lib/api/statement-counterparties.js';
  import WorkflowHeader from '#lib/components/WorkflowHeader.svelte';

  let job: CounterpartyJob | null = null;
  let error = '';
  let stopped = false;
  let timer: ReturnType<typeof setTimeout>;
  $: processed = (job?.applied ?? 0) + (job?.failed ?? 0);

  onMount(() => {
    const id = page.params.id;
    if (id) void poll(id);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  });

  async function poll(id: string) {
    error = '';
    try {
      const result = await getCounterpartyJob(id);
      if (stopped) return;
      job = result;
      if (job.status === 'pending' || job.status === 'running') {
        timer = setTimeout(() => void poll(id), 1200);
      }
    } catch (e) {
      if (!stopped) error = e instanceof Error ? e.message : 'Could not read the job.';
    }
  }

  function transactionLink(base: string | null, id: number) {
    if (!base) return null;
    try {
      const url = new URL(base);
      if (!['https:', 'http:'].includes(url.protocol)) return null;
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
  <WorkflowHeader title="Counterparty update status">
    <a slot="actions" href={resolve('/(app)/blik/counterparties')} class="btn btn-sm">New preview</a
    >
  </WorkflowHeader>

  {#if error}
    <div role="alert" class="alert alert-warning">
      {error}
      <button class="btn btn-sm" on:click={() => page.params.id && poll(page.params.id)}>
        Retry status
      </button>
    </div>
  {/if}

  {#if job}
    <div class="card bg-base-100 gap-3 p-4">
      <p role="status">
        {job.status === 'done'
          ? 'Finished'
          : job.status === 'failed'
            ? 'Stopped'
            : job.status === 'pending'
              ? 'Queued'
              : 'Updating'} · {processed} of {job.total} processed
      </p>
      <progress
        class="progress progress-primary w-full"
        value={processed}
        max={job.total}
        aria-label="Updates processed"
      ></progress>
      <p class="text-sm">{job.applied} applied · {job.failed} failed</p>
      {#if job.status === 'failed' && processed < job.total}
        <p class="text-sm">
          {job.total - processed} updates remain unprocessed. Run a fresh preview before continuing.
        </p>
      {/if}
    </div>
    {#if job.results.length}
      <ul
        class="card bg-base-100 divide-base-300 divide-y px-4"
        aria-label="Individual update results"
      >
        {#each job.results as result, index (`${result.transaction_id}-${index}`)}
          <li
            class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-3 [overflow-wrap:anywhere]"
          >
            {#if result.transaction_id > 0}
              {@const link = transactionLink(job.firefly_url, result.transaction_id)}
              {#if link}
                <a class="link" href={link} target="_blank" rel="external noopener noreferrer"
                  >Transaction #{result.transaction_id}</a
                >
              {:else}
                <span>Transaction #{result.transaction_id}</span>
              {/if}
            {/if}
            <span class="text-sm">{result.reason ?? 'Updated'}</span>
          </li>
        {/each}
      </ul>
    {/if}
  {:else if !error}
    <p role="status">Loading status…</p>
  {/if}
</div>
