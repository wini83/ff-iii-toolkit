<script lang="ts">
  import { goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import {
    listStatementAccounts,
    previewCounterparties,
    startCounterpartyJob,
    type CounterpartyCandidate,
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
  let accountChoices: Record<string, number> = {};
  let filter = 'actionable';
  let busy = false;
  let error = '';

  const matchLabels: Record<CounterpartyCandidate['match'], string> = {
    merchant: 'Exact merchant name',
    statement: 'Exact statement description',
    both: 'Exact merchant and statement',
    prefix: 'Partial statement match'
  };

  function groupRows(rows: CounterpartyPreview['rows']) {
    const groups = new Map<
      string,
      { merchant: string; candidates: CounterpartyCandidate[]; count: number }
    >();
    for (const row of rows) {
      if (!row.choice_group) continue;
      const group = groups.get(row.choice_group);
      if (group) {
        group.count += 1;
      } else {
        groups.set(row.choice_group, {
          merchant: row.card_merchant ?? row.description,
          candidates: row.candidates?.filter((candidate) => candidate.match !== 'prefix') ?? [],
          count: 1
        });
      }
    }
    return [...groups];
  }

  function reasonCounts(rows: CounterpartyPreview['rows'], choices: Record<string, number>) {
    const counts = new Map<string, number>();
    for (const row of rows) {
      if (row.status === 'review' && !(row.choice_group && choices[row.choice_group])) {
        counts.set(row.reason, (counts.get(row.reason) ?? 0) + 1);
      }
    }
    return [...counts].sort((a, b) => b[1] - a[1]);
  }

  $: groups = groupRows(preview?.rows ?? []);
  $: chosenCount = groups.reduce(
    (count, [id, group]) => count + (accountChoices[id] ? group.count : 0),
    0
  );
  $: readyCount = (preview?.ready ?? 0) + chosenCount;
  $: reviewReasons = reasonCounts(preview?.rows ?? [], accountChoices);

  $: visibleRows =
    preview?.rows.filter((row) => {
      const status = row.choice_group && accountChoices[row.choice_group] ? 'ready' : row.status;
      return filter === 'actionable' ? status !== 'skipped' : status === filter;
    }) ?? [];

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
    accountChoices = {};
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
    if (busy || !preview || !readyCount || !submitted) return;
    const evidence = submitted;
    const revision = preview.revision;
    const choices = accountChoices;
    invalidatePreview();
    busy = true;
    error = '';
    try {
      const job = await startCounterpartyJob(evidence, revision, choices);
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
          <strong>{readyCount} ready</strong>
          <span>{preview.review - chosenCount} to review</span>
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
        {#if reviewReasons.length}
          <details class="text-sm">
            <summary class="cursor-pointer">Reasons for review</summary>
            <ul class="mt-2 list-inside list-disc">
              {#each reviewReasons as [reason, count] (reason)}
                <li>{count} · {reason}</li>
              {/each}
            </ul>
          </details>
        {/if}
        {#if readyCount}
          <button type="button" class="btn btn-primary w-fit" on:click={apply} disabled={busy}>
            Apply {readyCount}
            {readyCount === 1 ? 'change' : 'changes'}
          </button>
        {/if}
      </div>

      {#if groups.length}
        <section aria-label="Choose accounts for card payments" class="card bg-base-100 gap-3 p-4">
          <h3 class="font-semibold">Choose accounts for card payments</h3>
          {#each groups as [id, group] (id)}
            <label class="block text-sm [overflow-wrap:anywhere]">
              {group.merchant} · {group.count}
              {group.count === 1 ? 'payment' : 'payments'}
              <select
                class="select mt-1 block w-full"
                value={accountChoices[id] ?? ''}
                on:change={(event) => {
                  const next = { ...accountChoices };
                  const value = Number(event.currentTarget.value);
                  if (value) next[id] = value;
                  else delete next[id];
                  accountChoices = next;
                }}
              >
                <option value="">Leave for review</option>
                {#each group.candidates as candidate (candidate.id)}
                  <option value={candidate.id}>
                    {candidate.name} · #{candidate.id} · {matchLabels[candidate.match]} · {candidate.prior_assignments}
                    existing in this period
                  </option>
                {/each}
              </select>
            </label>
          {/each}
        </section>
      {/if}

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
        {@const chosen = row.choice_group
          ? row.candidates?.find(
              (candidate) => candidate.id === accountChoices[row.choice_group ?? '']
            )
          : undefined}
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
          {#if chosen || row.proposed_account}
            <p class="text-sm">
              {row.current_account ?? 'Unknown'} →
              <strong>{chosen?.name ?? row.proposed_account}</strong>
            </p>
          {/if}
          <p class="text-base-content/70 text-sm">
            {chosen ? 'Account selected for this group.' : row.reason}
          </p>
          {#if row.candidates?.length}
            <details class="text-base-content/70 text-sm">
              <summary class="cursor-pointer">Matching accounts</summary>
              <ul class="mt-1 list-inside list-disc">
                {#each row.candidates as candidate (candidate.id)}
                  <li>{candidate.name} · #{candidate.id} · {matchLabels[candidate.match]}</li>
                {/each}
              </ul>
            </details>
          {/if}
        </article>
      {:else}
        <p role="status">No rows in this view.</p>
      {/each}
    </section>
  {/if}
</div>
