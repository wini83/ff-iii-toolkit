<script lang="ts">
  import { onMount } from 'svelte';
  import { replaceState } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { Icon } from '@steeze-ui/svelte-icon';
  import * as icons from '@steeze-ui/heroicons';
  import * as api from '$lib/api/velobank';
  import type { AccountMappings, AccountOption, VeloBankPreview } from '$lib/api/velobank';

  let selectedFile: File | null = null;
  let fileInput: HTMLInputElement;
  let result: VeloBankPreview | null = null;
  let mappings: AccountMappings = {};
  let accountOptions: AccountOption[] = [];
  let statementName = '';
  let newIban = '';
  let newAccountName = '';
  let busy = true;
  let error = '';
  let accountError = '';
  let message = '';
  let mappingDirty = false;
  let zip = false;
  let chunkSize = 60;
  let page = 1;
  let now = Date.now();

  $: expired = result !== null && Date.parse(result.expires_at) <= now;
  $: ownMappings = Object.entries(mappings).filter(([iban]) => iban !== result?.account_iban);
  $: visibleRows = result?.preview.slice((page - 1) * 50, page * 50) ?? [];
  $: totalPages = Math.max(1, Math.ceil((result?.record_count ?? 0) / 50));
  $: reviewCount = result?.preview.filter((row) => row.needs_review).length ?? 0;
  $: detectedIbans = [
    ...new Set(result?.preview.flatMap((row) => [row.source_iban, row.destination_iban]) ?? [])
  ].filter((iban) => iban && iban !== result?.account_iban && !mappings[iban]);

  function failure(err: unknown) {
    return err instanceof Error ? err.message : 'The request failed. Please retry.';
  }

  function updateUrl(fileId?: string) {
    const url = new URL(window.location.href);
    if (fileId) url.searchParams.set('file_id', fileId);
    else url.searchParams.delete('file_id');
    replaceState(
      url.searchParams.size
        ? resolve(`/tools/velobank?${url.searchParams.toString()}`)
        : resolve('/tools/velobank'),
      {}
    );
  }

  function currentMappings(): AccountMappings {
    const accounts = { ...mappings };
    if (result) {
      if (statementName.trim()) accounts[result.account_iban] = statementName.trim();
      else delete accounts[result.account_iban];
    }
    return accounts;
  }

  function acceptPreview(preview: VeloBankPreview) {
    result = preview;
    statementName =
      mappings[preview.account_iban] ??
      accountOptions.find(
        (account) => account.iban?.replace(/\s/g, '').toUpperCase() === preview.account_iban
      )?.name ??
      '';
    mappingDirty = statementName !== preview.account_name;
    page = 1;
    now = Date.now();
    updateUrl(preview.file_id);
  }

  async function loadAccounts() {
    accountError = '';
    try {
      accountOptions = await api.getAccounts();
    } catch (err) {
      accountError = failure(err);
    }
  }

  onMount(() => {
    const timer = window.setInterval(() => {
      now = Date.now();
    }, 1000);
    void (async () => {
      try {
        const [accounts] = await Promise.all([api.getMappings(), loadAccounts()]);
        mappings = accounts;
        const fileId = new URL(window.location.href).searchParams.get('file_id');
        if (fileId) acceptPreview(await api.getPreview(fileId));
      } catch (err) {
        error = failure(err);
      } finally {
        busy = false;
      }
    })();
    return () => window.clearInterval(timer);
  });

  async function parseFile() {
    if (!selectedFile) return;
    error = '';
    message = '';
    if (!selectedFile.name.toLowerCase().endsWith('.pdf') || selectedFile.size > 10 * 1024 * 1024) {
      error = 'Choose a PDF no larger than 10 MiB.';
      return;
    }
    busy = true;
    try {
      acceptPreview(await api.uploadPdf(selectedFile));
      selectedFile = null;
      fileInput.value = '';
    } catch (err) {
      error = failure(err);
    } finally {
      busy = false;
    }
  }

  function addOwnAccount() {
    const iban = 'PL' + newIban.replace(/\s/g, '').toUpperCase().replace(/^PL/, '');
    if (!/^PL\d{26}$/.test(iban) || !newAccountName.trim()) {
      error = 'Enter a Polish account number and its exact Firefly account name.';
      return;
    }
    if (iban === result?.account_iban || mappings[iban]) {
      error = 'This account is already mapped.';
      return;
    }
    mappings = { ...mappings, [iban]: newAccountName.trim() };
    newIban = '';
    newAccountName = '';
    error = '';
    mappingDirty = true;
  }

  function removeOwnAccount(iban: string) {
    const accounts = { ...mappings };
    delete accounts[iban];
    mappings = accounts;
    mappingDirty = true;
  }

  async function refreshPreview(save = false) {
    if (!result || expired) return;
    busy = true;
    error = '';
    message = '';
    try {
      const accounts = currentMappings();
      result = await api.configurePreview(result.file_id, accounts);
      mappingDirty = false;
      if (save) {
        mappings = await api.saveMappings(accounts);
        message = 'Account mappings saved for your next import.';
      }
    } catch (err) {
      error = failure(err);
    } finally {
      busy = false;
    }
  }

  async function download() {
    if (!result || expired || !statementName.trim()) return;
    if (zip && (!Number.isInteger(chunkSize) || chunkSize < 1 || chunkSize > 1000)) {
      error = 'Choose between 1 and 1000 operations per file.';
      return;
    }
    busy = true;
    error = '';
    message = '';
    try {
      const accounts = currentMappings();
      result = await api.configurePreview(result.file_id, accounts);
      mappingDirty = false;
      const { blob, filename } = await api.downloadCsv(
        result.file_id,
        accounts,
        zip ? chunkSize : undefined
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      message =
        'Download ready. Review account mappings in Firefly Data Importer before importing.';
    } catch (err) {
      error = failure(err);
    } finally {
      busy = false;
    }
  }

  async function discard() {
    if (!result) return;
    busy = true;
    error = '';
    try {
      await api.discardPreview(result.file_id);
      result = null;
      statementName = '';
      message = '';
      updateUrl();
    } catch (err) {
      error = failure(err);
    } finally {
      busy = false;
    }
  }
</script>

<svelte:head><title>VeloBank Import · Firefly Toolkit</title></svelte:head>

<div class="mx-auto flex w-full max-w-7xl flex-col gap-6">
  <div class="flex items-center gap-4">
    <div class="bg-secondary/12 text-secondary rounded-2xl p-4">
      <Icon src={icons.DocumentArrowUp} class="h-8 w-8" />
    </div>
    <div>
      <h2 class="text-3xl font-semibold">VeloBank Import</h2>
      <p class="text-base-content/70 mt-1">
        Upload account history, review operations and prepare a CSV for Firefly.
      </p>
    </div>
  </div>
  {#if error}<div role="alert" class="alert alert-error"><span>{error}</span></div>{/if}
  {#if message}<div role="status" class="alert alert-success"><span>{message}</span></div>{/if}

  <section class="card bg-base-100 border-base-200 border shadow-sm">
    <div class="card-body">
      <h3 class="card-title">Account-history PDF</h3>
      <p class="text-base-content/70 text-sm">
        Use a text-based VeloBank “Historia rachunku” PDF. Include all operation types and complete
        days. Maximum 10 MiB, 50 pages and 5000 operations.
      </p>
      <div class="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div class="form-control flex-1">
          <label class="label" for="velobank-file">PDF file</label><input
            bind:this={fileInput}
            id="velobank-file"
            type="file"
            accept=".pdf,application/pdf"
            class="file-input file-input-bordered w-full"
            disabled={busy}
            on:change={(event) => {
              selectedFile = event.currentTarget.files?.[0] ?? null;
            }}
          />
        </div>
        <button class="btn btn-primary" disabled={busy || !selectedFile} on:click={parseFile}
          >{#if busy}<span class="loading loading-spinner loading-sm"></span>{/if}Read PDF</button
        >
      </div>
      <p class="text-base-content/60 text-xs">
        Processed on this toolkit server. The source PDF is removed after reading. Preview data
        expires after 30 minutes or a server restart.
      </p>
    </div>
  </section>

  {#if result}
    {#if expired}<div role="alert" class="alert alert-warning">
        <span>This preview has expired. Upload the PDF again to continue.</span>
      </div>{/if}
    <section class="card bg-base-100 border-base-200 border shadow-sm">
      <div class="card-body gap-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 class="card-title">Review account history</h3>
            <p class="text-base-content/70 mt-1 text-sm break-all">{result.account_iban}</p>
            <p class="text-sm">
              {result.start_date} – {result.end_date} · {result.record_count} operations
            </p>
          </div>
          <button class="btn btn-ghost btn-sm" disabled={busy} on:click={discard}
            >Discard preview</button
          >
        </div>
        <div class="flex flex-wrap gap-3">
          {#each result.totals as total (total.currency)}<div
              class="bg-base-200 rounded-xl px-4 py-3"
            >
              <div class="text-sm font-semibold">{total.currency}</div>
              <div>Debits: {total.debits}</div>
              <div>Credits: {total.credits}</div>
            </div>{/each}
          <div class="bg-base-200 rounded-xl px-4 py-3">
            <div class="text-sm font-semibold">Needs review</div>
            <div>{reviewCount} counterparties</div>
          </div>
        </div>
        <p class="text-base-content/60 text-xs">
          Preview expires at {new Date(result.expires_at).toLocaleTimeString()}.
        </p>
      </div>
    </section>

    <section class="card bg-base-100 border-base-200 border shadow-sm">
      <div class="card-body gap-4">
        <h3 class="card-title">Map your accounts</h3>
        <p class="text-base-content/70 text-sm">
          Choose the existing Firefly asset account for this statement. Map other own accounts so
          card repayments and transfers use the correct accounts.
        </p>
        {#if accountError}<div role="status" class="alert alert-info">
            <div>{accountError} You can enter exact account names manually.</div>
            <button class="btn btn-sm btn-ghost" disabled={busy} on:click={loadAccounts}
              >Retry</button
            >
          </div>{/if}
        <datalist id="firefly-accounts"
          >{#each accountOptions as account (account.id)}<option value={account.name}
              >{account.iban ?? 'Asset account'}</option
            >{/each}</datalist
        >
        <div class="form-control max-w-xl">
          <label class="label" for="statement-account">Firefly account for this statement</label
          ><input
            id="statement-account"
            list="firefly-accounts"
            class="input input-bordered w-full"
            bind:value={statementName}
            on:input={() => {
              mappingDirty = true;
            }}
            placeholder="Exact Firefly account name"
            disabled={busy || expired}
          />
        </div>
        {#if ownMappings.length}<div class="flex flex-col gap-2">
            {#each ownMappings as [iban, name] (iban)}<div
                class="bg-base-200 flex flex-wrap items-center justify-between gap-2 rounded-xl p-3"
              >
                <div class="min-w-0">
                  <div class="text-sm break-all">{iban}</div>
                  <strong>{name}</strong>
                </div>
                <button
                  class="btn btn-ghost btn-sm"
                  disabled={busy || expired}
                  on:click={() => removeOwnAccount(iban)}>Remove</button
                >
              </div>{/each}
          </div>{/if}
        <fieldset disabled={busy || expired} class="border-base-300 rounded-xl border p-4">
          <legend class="px-2 text-sm font-semibold">Add another own account</legend>
          <div class="grid gap-3 md:grid-cols-2">
            <div>
              <label class="label" for="other-iban">Polish account number / IBAN</label><input
                id="other-iban"
                list="detected-ibans"
                class="input input-bordered w-full"
                bind:value={newIban}
                placeholder="PL…"
              />
            </div>
            <div>
              <label class="label" for="other-name">Firefly asset account name</label><input
                id="other-name"
                list="firefly-accounts"
                class="input input-bordered w-full"
                bind:value={newAccountName}
                placeholder="Select or enter an account name"
              />
            </div>
          </div>
          <datalist id="detected-ibans"
            >{#each detectedIbans as iban (iban)}<option value={iban}></option>{/each}</datalist
          ><button
            class="btn btn-outline btn-sm mt-3"
            disabled={!newIban.trim() || !newAccountName.trim()}
            on:click={addOwnAccount}>Add own account</button
          >
          <p class="text-base-content/60 mt-2 text-xs">
            Only add accounts that belong to you. Suggestions come from transfer descriptions.
          </p>
        </fieldset>
        <div class="flex flex-wrap gap-2">
          <button
            class="btn btn-outline"
            disabled={busy || expired}
            on:click={() => refreshPreview()}>Update preview</button
          ><button
            class="btn btn-secondary"
            disabled={busy || expired || !statementName.trim()}
            on:click={() => refreshPreview(true)}>Save mappings for next time</button
          >
        </div>
        {#if mappingDirty}<p role="status" class="text-warning text-sm">
            Account mapping changed. Update the preview to review the new result.
          </p>{/if}
      </div>
    </section>

    <section class="card bg-base-100 border-base-200 border shadow-sm">
      <div class="card-body gap-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h3 class="card-title">Operations</h3>
          <button
            class="btn btn-primary"
            disabled={busy || expired || !statementName.trim() || mappingDirty}
            on:click={download}
            >{#if busy}<span class="loading loading-spinner loading-sm"></span>{/if}Download {zip
              ? 'ZIP'
              : 'CSV'}</button
          >
        </div>
        <div class="alert alert-warning text-sm">
          <div>
            {#each result.warnings as warning}<p>{warning}</p>{/each}
          </div>
        </div>
        <details class="border-base-300 rounded-xl border p-3">
          <summary class="cursor-pointer text-sm font-semibold"
            >Export options and importer setup</summary
          >
          <div class="mt-3 space-y-3 text-sm">
            <label class="flex items-center gap-2"
              ><input
                type="checkbox"
                class="checkbox checkbox-sm"
                bind:checked={zip}
                disabled={busy}
              />Split CSV into a ZIP package</label
            >{#if zip}<div>
                <label for="chunk-size" class="label">Operations per CSV file</label><input
                  id="chunk-size"
                  type="number"
                  min="1"
                  max="1000"
                  class="input input-bordered input-sm"
                  bind:value={chunkSize}
                  disabled={busy}
                />
              </div>{/if}
            <p>
              In Firefly Data Importer choose File, UTF-8, delimiter “;”, headers enabled and date
              format “Y-m-d”. Map Date, Amount, Currency, Description, both account names and IBANs,
              and External ID. Map Booking date if supported; skip the helper Payee column. Enable
              identifier-based duplicate detection.
            </p>
            <p>
              Check a small first batch and save the importer's configuration. Downloading does not
              create transactions in Firefly.
            </p>
          </div>
        </details>
        <div class="overflow-x-auto">
          <table class="table-sm table">
            <thead
              ><tr
                ><th>Date / booked</th><th>Amount</th><th>Counterparty</th><th>Description</th><th
                  >Source → destination</th
                ></tr
              ></thead
            ><tbody
              >{#each visibleRows as row (row.id)}<tr
                  ><td class="whitespace-nowrap"
                    >{row.date}
                    <div class="text-base-content/60 text-xs">{row.booking_date}</div></td
                  ><td class="whitespace-nowrap"
                    ><span class:text-success={Number(row.amount) > 0}
                      >{row.amount} {row.currency}</span
                    ></td
                  ><td class="min-w-40"
                    >{row.payee}{#if row.needs_review}<div
                        class="badge badge-warning badge-sm mt-1"
                      >
                        Needs review
                      </div>{/if}</td
                  ><td class="max-w-lg min-w-64 break-words whitespace-normal">{row.description}</td
                  ><td class="min-w-48"
                    ><div>
                      {row.source_account || 'Choose account'} → {row.destination_account ||
                        'Choose account'}
                    </div>
                    {#if row.source_iban}<div class="text-base-content/60 text-xs break-all">
                        From: {row.source_iban}
                      </div>{/if}{#if row.destination_iban}<div
                        class="text-base-content/60 text-xs break-all"
                      >
                        To: {row.destination_iban}
                      </div>{/if}</td
                  ></tr
                >{/each}</tbody
            >
          </table>
        </div>
        <div class="flex items-center justify-between">
          <button
            class="btn btn-outline btn-sm"
            disabled={page <= 1}
            on:click={() => {
              page -= 1;
            }}>Previous</button
          ><span class="text-sm">Page {page} of {totalPages}</span><button
            class="btn btn-outline btn-sm"
            disabled={page >= totalPages}
            on:click={() => {
              page += 1;
            }}>Next</button
          >
        </div>
      </div>
    </section>
  {/if}
</div>
