<script lang="ts">
  import { goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import {
    listStatementAccounts,
    previewCounterparties,
    startCounterpartyJob,
    type CounterpartyPreview,
    type StatementAccount,
    type StatementEvidence
  } from '#lib/api/statement-counterparties.js';
  import WorkflowHeader from '#lib/components/WorkflowHeader.svelte';

  let accounts: StatementAccount[] | null = null;
  let accountId = '';
  let singleAccountConfirmed = false;
  let files: FileList | undefined;
  let submitted: StatementEvidence | null = null;
  let preview: CounterpartyPreview | null = null;
  let filter = 'actionable';
  let busy = false;
  let error = '';

  $: visibleRows =
    preview?.rows.filter((row) =>
      filter === 'actionable' ? row.status !== 'skipped' : row.status === filter
    ) ?? [];

  onMount(() => void loadAccounts());

  async function loadAccounts() {
    busy = true;
    error = '';
    try {
      accounts = await listStatementAccounts();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Could not load accounts.';
    } finally {
      busy = false;
    }
  }

  function invalidatePreview() {
    preview = null;
    submitted = null;
  }

  async function compare(event: SubmitEvent) {
    event.preventDefault();
    if (busy || !accountId || !files?.[0]) return;
    const evidence = { file: files[0], accountId, singleAccountConfirmed };
    invalidatePreview();
    filter = 'actionable';
    busy = true;
    error = '';
    try {
      preview = await previewCounterparties(evidence);
      submitted = evidence;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Could not prepare the preview.';
    } finally {
      busy = false;
    }
  }

  async function apply() {
    if (busy || !preview?.ready || !submitted) return;
    const evidence = submitted;
    const revision = preview.revision;
    invalidatePreview();
    busy = true;
    error = '';
    try {
      const job = await startCounterpartyJob(evidence, revision);
      await goto(resolve('/(app)/blik/counterparties/jobs/[id]', { id: job.id }));
    } catch (e) {
      error = e instanceof Error ? e.message : 'Could not confirm the job started.';
    } finally {
      busy = false;
    }
  }
</script>

<div class="min-w-0 space-y-4">
  <WorkflowHeader title="Assign bank counterparties">
    <span slot="description">
      Match an Alior statement to Firefly and fill in unknown counterparties.
    </span>
    <a slot="actions" href={resolve('/(app)/blik/upload')} class="btn btn-sm"> Back to BLIK </a>
  </WorkflowHeader>

  {#if error}<p role="alert" class="alert alert-warning">{error}</p>{/if}
  {#if !accounts && !busy}
    <button class="btn btn-sm" on:click={loadAccounts}>Retry accounts</button>
  {:else if accounts?.length === 0}
    <p role="status">Add an IBAN or bank account number to at least one Firefly asset account.</p>
  {/if}

  <form class="card bg-base-100 p-4" on:submit={compare} on:change={invalidatePreview}>
    <fieldset disabled={busy || !accounts?.length} class="grid gap-3 md:grid-cols-2">
      <legend class="sr-only">Alior statement</legend>
      <label class="min-w-0 text-sm">
        Firefly account
        <select
          class="select mt-1 w-full"
          bind:value={accountId}
          on:change={() => (singleAccountConfirmed = false)}
          required
        >
          <option value="" disabled>Select an account</option>
          {#each accounts ?? [] as account (account.id)}
            <option value={String(account.id)}>{account.name}</option>
          {/each}
        </select>
      </label>
      <label class="min-w-0 text-sm">
        Alior CSV
        <input
          type="file"
          accept=".csv,text/csv"
          class="file-input mt-1 w-full"
          bind:files
          on:change={() => (singleAccountConfirmed = false)}
          required
        />
      </label>
      <label class="flex items-start gap-2 text-sm md:col-span-2">
        <input type="checkbox" class="checkbox checkbox-sm" bind:checked={singleAccountConfirmed} />
        This export contains only transactions from the selected account. Confirm to include card rows
        without bank account numbers.
      </label>
      <button class="btn btn-primary md:w-fit" type="submit" disabled={!accountId || !files?.[0]}>
        Preview changes
      </button>
    </fieldset>
  </form>

  {#if busy}
    <p class="text-sm" role="status">Working…</p>
  {/if}

  {#if preview}
    <section aria-label="Proposed account changes" class="space-y-3">
      <div class="card bg-base-100 gap-3 p-4">
        <div class="flex flex-wrap gap-x-4 gap-y-1">
          <strong>{preview.ready} ready</strong>
          <span>{preview.review} to review</span>
          <span>{preview.skipped} skipped</span>
        </div>
        {#if preview.omitted}
          <p class="text-base-content/70 text-sm">
            {preview.omitted} statement rows could not be compared using a bank account number or a matching
            Firefly card payment.
          </p>
        {/if}
        {#if preview.skipped_multipart || preview.skipped_invalid}
          <p class="text-base-content/70 text-sm">
            Firefly records omitted: {preview.skipped_multipart} split and
            {preview.skipped_invalid} invalid.
          </p>
        {/if}
        {#if preview.ready}
          <button type="button" class="btn btn-primary w-fit" on:click={apply} disabled={busy}>
            Apply {preview.ready}
            {preview.ready === 1 ? 'change' : 'changes'}
          </button>
        {/if}
      </div>

      <label class="block text-sm">
        Show
        <select class="select mt-1 block w-full sm:w-auto" bind:value={filter}>
          <option value="actionable">Ready and to review</option>
          <option value="ready">Ready</option>
          <option value="review">To review</option>
          <option value="skipped">Skipped rows with details</option>
        </select>
      </label>

      {#each visibleRows as row (row.row_id)}
        <article class="card bg-base-100 gap-2 p-4 [overflow-wrap:anywhere]">
          <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <strong class="font-mono">{row.amount} {row.currency}</strong>
            <span class="text-base-content/70 text-sm">
              {row.date} · Row {row.row_id}{row.transaction_id ? ` · #${row.transaction_id}` : ''}
            </span>
          </div>
          <p>{row.description}</p>
          {#if row.firefly_description && row.firefly_description !== row.description}
            <p class="text-base-content/70 text-sm">Firefly: {row.firefly_description}</p>
          {/if}
          {#if row.proposed_account}
            <p class="text-sm">
              {row.current_account ?? 'Unknown'} → <strong>{row.proposed_account}</strong>
            </p>
          {/if}
          <p class="text-base-content/70 text-sm">{row.reason}</p>
        </article>
      {:else}
        <p role="status">No rows in this view.</p>
      {/each}
    </section>
  {/if}
</div>
