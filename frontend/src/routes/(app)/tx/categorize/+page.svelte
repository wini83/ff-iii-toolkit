<script lang="ts">
  import { onMount } from 'svelte';
  import { afterNavigate, goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { Icon } from '@steeze-ui/svelte-icon';
  import * as icons from '@steeze-ui/heroicons';

  import {
    getScreeningMonth,
    assignCategory,
    applyTag,
    getCategorySuggestions
  } from '#lib/api/tx.js';
  import type { operations, components } from '#lib/api/schema.js';

  type ScreeningMonthResponse =
    operations['get_screening_month_api_tx_screening_get']['responses'][200]['content']['application/json'];

  type TxTag = components['schemas']['TxTag'];
  type CategorySuggestion = components['schemas']['CategorySuggestionDto'];

  let loading = true;
  let error: string | null = null;
  let initialized = false;
  let applyingCategory = false;
  let applyingTag = false;
  let updateError: string | null = null;
  let loadRequestId = 0;
  $: busy = applyingCategory || applyingTag;

  let data: ScreeningMonthResponse | null = null;
  let initialCount = 0;
  let categorySuggestions: CategorySuggestion[] = [];
  let categorySuggestionsLoading = false;
  let categorySuggestionsError: string | null = null;
  let categorySuggestionsRequestId = 0;

  let year: number;
  let month: number;
  let draftYear: number;
  let draftMonth: number;

  let cursor = 0;
  let selectedCategories: Record<string, string> = {};

  const PRIMARY_TAGS = new Set(['blik_done', 'allegro_done']);
  const isPrimary = (tag: string) => PRIMARY_TAGS.has(tag);

  const TERMINAL_TAGS = new Set<TxTag>(['action_req']);
  const MONTH_OPTIONS = [
    { value: 1, label: 'January' },
    { value: 2, label: 'February' },
    { value: 3, label: 'March' },
    { value: 4, label: 'April' },
    { value: 5, label: 'May' },
    { value: 6, label: 'June' },
    { value: 7, label: 'July' },
    { value: 8, label: 'August' },
    { value: 9, label: 'September' },
    { value: 10, label: 'October' },
    { value: 11, label: 'November' },
    { value: 12, label: 'December' }
  ] as const;

  $: currentTx = data?.transactions[cursor] ?? null;
  $: currentTxId = currentTx ? toIdString(currentTx.id) : null;
  $: progressValue = data ? initialCount - data.remaining : 0;
  $: currentCategoryName = currentTx?.category?.trim() ?? '';
  $: normalizedCurrentCategoryName = currentCategoryName.toLowerCase();
  $: displayedCategorySuggestions = categorySuggestions.slice(0, 3);
  $: selectedCategoryName =
    data?.categories.find(
      (category) => String(category.id) === selectedCategories[currentTxId ?? '']
    )?.name ?? '';

  function toIdString(id: string | number): string {
    return String(id);
  }

  function formatMoney(
    amount: number,
    currencyCode: string | null | undefined,
    currencySymbol: string | null | undefined
  ) {
    const safeCode = currencyCode && currencyCode.trim() ? currencyCode.trim() : 'PLN';
    const safeSymbol = currencySymbol && currencySymbol.trim() ? currencySymbol.trim() : safeCode;
    const formatted = new Intl.NumberFormat('en-GB', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 20
    }).format(amount);
    return `${formatted} ${safeSymbol}${safeSymbol === safeCode ? '' : ` (${safeCode})`}`;
  }

  function splitNotesLines(notes: string | null | undefined) {
    if (!notes || !notes.trim()) return [];
    return notes
      .replaceAll('\\n', '\n')
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
  }

  function ensureCategoryState(txId: string | number) {
    const normalizedId = toIdString(txId);

    if (selectedCategories[normalizedId] === undefined) {
      selectedCategories = {
        ...selectedCategories,
        [normalizedId]: ''
      };
    }
  }

  function clearCategorySuggestions() {
    categorySuggestions = [];
    categorySuggestionsError = null;
    categorySuggestionsLoading = false;
  }

  function formatSuggestionConfidence(score: number) {
    const normalizedScore = score > 1 ? score : score * 100;
    const percent = Math.max(0, Math.min(100, Math.round(normalizedScore)));

    return `${percent}%`;
  }

  function isCurrentCategorySuggestion(suggestion: CategorySuggestion) {
    if (!normalizedCurrentCategoryName) return false;

    return suggestion.category_name.trim().toLowerCase() === normalizedCurrentCategoryName;
  }

  async function loadCategorySuggestions(txId: number | string | null) {
    const requestId = ++categorySuggestionsRequestId;

    if (txId === null) {
      clearCategorySuggestions();
      return;
    }

    categorySuggestions = [];
    categorySuggestionsLoading = true;
    categorySuggestionsError = null;

    try {
      const response = await getCategorySuggestions(Number(txId));

      if (requestId !== categorySuggestionsRequestId) return;

      categorySuggestions = response.suggestions.slice(0, 3);
    } catch (e) {
      if (requestId !== categorySuggestionsRequestId) return;

      categorySuggestions = [];
      categorySuggestionsError = e instanceof Error ? e.message : 'Failed to load suggestions';
    } finally {
      if (requestId === categorySuggestionsRequestId) {
        categorySuggestionsLoading = false;
      }
    }
  }

  function removeFromQueue(txId: number | string) {
    if (!data) return;
    const transactions = data.transactions.filter((transaction) => transaction.id !== txId);
    data = { ...data, transactions, remaining: transactions.length };
    cursor = Math.min(cursor, Math.max(0, transactions.length - 1));
    delete selectedCategories[toIdString(txId)];
    if (transactions[cursor]) ensureCategoryState(transactions[cursor].id);
    void loadCategorySuggestions(transactions[cursor]?.id ?? null);
  }

  function resolveYearMonth() {
    const params = page.url.searchParams;
    const now = new Date();

    year = Number(params.get('year')) || now.getFullYear();
    month = Number(params.get('month')) || now.getMonth() + 1;
    draftYear = year;
    draftMonth = month;
  }

  async function load() {
    const requestId = ++loadRequestId;
    ++categorySuggestionsRequestId;
    clearCategorySuggestions();
    loading = true;
    error = null;
    updateError = null;
    data = null;
    initialCount = 0;
    cursor = 0;
    selectedCategories = {};

    try {
      const response = await getScreeningMonth(year, month);
      if (requestId !== loadRequestId) return;
      data = response;
      initialCount = response?.transactions.length ?? 0;
      if (response?.transactions[0]) {
        ensureCategoryState(response.transactions[0].id);
        void loadCategorySuggestions(response.transactions[0].id);
      }
    } catch (e) {
      if (requestId !== loadRequestId) return;
      error = e instanceof Error ? e.message : 'Failed to load screening queue';
    } finally {
      if (requestId === loadRequestId) loading = false;
    }
  }

  async function applyCategory(categoryId: number) {
    if (!currentTx || !data || busy || loading) return;
    const txId = currentTx.id;
    const queue = data;
    applyingCategory = true;
    updateError = null;
    try {
      await assignCategory(Number(txId), categoryId);
      if (data !== queue) return;
      removeFromQueue(txId);
      window.dispatchEvent(
        new CustomEvent('toast', {
          detail: { type: 'success', msg: 'Category applied' }
        })
      );
    } catch (err) {
      if (data !== queue) return;
      updateError = err instanceof Error ? err.message : 'Failed to apply category';
    } finally {
      applyingCategory = false;
    }
  }

  async function applyCurrent() {
    if (!currentTx || !data) return;

    const rawValue = selectedCategories[toIdString(currentTx.id)];
    const categoryId = Number(rawValue);
    if (!categoryId) return;

    await applyCategory(categoryId);
  }

  async function applySuggestedCategory(suggestion: CategorySuggestion) {
    if (!currentTx || !data) return;

    await applyCategory(Number(suggestion.category_id));
  }

  async function tagCurrentTx(tag: TxTag) {
    if (!currentTx || !data || busy || loading) return;
    const txId = currentTx.id;
    const queue = data;
    applyingTag = true;
    updateError = null;
    try {
      await applyTag(Number(txId), tag);
      if (data !== queue) return;
      if (TERMINAL_TAGS.has(tag)) {
        removeFromQueue(txId);
      } else {
        data = {
          ...data,
          transactions: data.transactions.map((transaction) =>
            transaction.id === txId
              ? { ...transaction, tags: Array.from(new Set([...transaction.tags, tag])) }
              : transaction
          )
        };
      }
      window.dispatchEvent(
        new CustomEvent('toast', {
          detail: { type: 'success', msg: `Tag applied: ${tag.replace('_', ' ')}` }
        })
      );
    } catch (err) {
      if (data !== queue) return;
      updateError = err instanceof Error ? err.message : 'Failed to apply tag';
    } finally {
      applyingTag = false;
    }
  }

  function prevTx() {
    if (cursor > 0 && data && !busy) {
      updateError = null;
      cursor--;
      ensureCategoryState(data.transactions[cursor].id);
      void loadCategorySuggestions(data.transactions[cursor].id);
    }
  }

  function nextTx() {
    if (data && cursor < data.transactions.length - 1 && !busy) {
      updateError = null;
      cursor++;
      ensureCategoryState(data.transactions[cursor].id);
      void loadCategorySuggestions(data.transactions[cursor].id);
    }
  }

  function prevMonth() {
    const d = new Date(year, month - 2, 1);
    goto(resolve(`tx/categorize?year=${d.getFullYear()}&month=${d.getMonth() + 1}`));
  }

  function nextMonth() {
    const d = new Date(year, month, 1);
    goto(resolve(`tx/categorize?year=${d.getFullYear()}&month=${d.getMonth() + 1}`));
  }

  function jumpToPeriod() {
    const nextYear = Number.isFinite(draftYear) ? Math.trunc(draftYear) : year;
    const nextMonth = Number.isFinite(draftMonth) ? Math.trunc(draftMonth) : month;

    if (nextMonth < 1 || nextMonth > 12) return;
    if (nextYear < 1) return;

    goto(resolve(`tx/categorize?year=${nextYear}&month=${nextMonth}`));
  }

  onMount(() => {
    resolveYearMonth();
    load();
    initialized = true;
  });

  afterNavigate(() => {
    if (!initialized) return;
    const now = new Date();
    const y = Number(page.url.searchParams.get('year')) || now.getFullYear();
    const m = Number(page.url.searchParams.get('month')) || now.getMonth() + 1;
    if (y && m && (y !== year || m !== month)) {
      resolveYearMonth();
      void load();
    }
  });
</script>

<svelte:head>
  <title>Transactions Categorize — Firefly Toolkit</title>
</svelte:head>

<div class="mx-auto flex w-full max-w-7xl min-w-0 flex-col gap-3">
  <section
    aria-label="Screening period"
    class="border-base-300 bg-base-100 rounded-box border px-3 py-2"
  >
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h2 class="text-base font-semibold">Transaction screening</h2>
      <form
        class="grid w-full min-w-0 grid-cols-[auto_minmax(0,1fr)_5rem_auto_auto] items-center gap-2 sm:flex sm:w-auto sm:shrink-0 sm:flex-nowrap"
        on:submit|preventDefault={jumpToPeriod}
      >
        <button
          type="button"
          class="btn btn-ghost btn-sm btn-square"
          aria-label="Previous month"
          disabled={loading || busy}
          on:click={prevMonth}
        >
          <Icon src={icons.ChevronLeft} class="h-4 w-4" />
        </button>
        <label class="sr-only" for="screening-month">Month</label>
        <select
          id="screening-month"
          class="select select-bordered select-sm w-full min-w-0 sm:w-32"
          bind:value={draftMonth}
          disabled={loading || busy}
        >
          {#each MONTH_OPTIONS as option}
            <option value={option.value}>{option.label}</option>
          {/each}
        </select>
        <label class="sr-only" for="screening-year">Year</label>
        <input
          id="screening-year"
          class="input input-bordered input-sm w-20"
          type="number"
          min="1"
          max="9999"
          step="1"
          bind:value={draftYear}
          disabled={loading || busy}
        />
        <button type="submit" class="btn btn-outline btn-sm" disabled={loading || busy}>Go</button>
        <button
          type="button"
          class="btn btn-ghost btn-sm btn-square"
          aria-label="Next month"
          disabled={loading || busy}
          on:click={nextMonth}
        >
          <Icon src={icons.ChevronRight} class="h-4 w-4" />
        </button>
      </form>
    </div>
    {#if !loading && !error}
      <div
        class="text-base-content/70 mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs"
        role="status"
        aria-label="Queue summary"
      >
        <span>
          <strong class="text-base-content">{data?.remaining ?? 0} remaining</strong> · {progressValue}
          completed of {initialCount} loaded
        </span>
        <span class="toolkit-data">{year}-{String(month).padStart(2, '0')}</span>
      </div>
    {/if}
  </section>

  {#if loading}
    <section
      class="border-base-300 bg-base-100 rounded-box space-y-3 border p-4"
      role="status"
      aria-label="Loading transactions"
    >
      <p class="text-sm">Loading transactions…</p>
      <div class="skeleton h-6 w-48"></div>
      <div class="skeleton h-24 w-full"></div>
      <div class="skeleton h-9 w-full"></div>
    </section>
  {:else if error}
    <div class="alert alert-error" role="alert">
      <Icon src={icons.ExclamationTriangle} class="h-5 w-5" />
      <span class="min-w-0 wrap-anywhere">{error}</span>
      <button class="btn btn-outline btn-sm" on:click={load}>Retry queue</button>
    </div>
  {:else if !currentTx || !currentTxId || !data || data.remaining === 0}
    <section class="border-base-300 bg-base-100 rounded-box border p-5" role="status">
      <h3 class="flex items-center gap-2 text-base font-semibold">
        <Icon src={icons.CheckCircle} class="text-success h-5 w-5" /> No transactions left
      </h3>
      <p class="text-base-content/70 mt-2 text-sm">
        The selected month has no transactions waiting for screening.
      </p>
    </section>
  {:else}
    <nav aria-label="Transaction queue" class="flex flex-wrap items-center justify-between gap-2">
      <span class="text-base-content/70 text-xs" aria-live="polite">
        Position {cursor + 1} of {data.transactions.length} in queue
      </span>
      <div class="flex items-center gap-2">
        <button
          class="btn btn-outline btn-sm"
          disabled={busy || cursor === 0}
          on:click={prevTx}
          aria-label="Previous transaction"
        >
          <Icon src={icons.ChevronLeft} class="h-4 w-4" /> Prev
        </button>
        <button
          class="btn btn-outline btn-sm"
          disabled={busy || cursor >= data.transactions.length - 1}
          on:click={nextTx}
          aria-label="Next transaction"
        >
          Next <Icon src={icons.ChevronRight} class="h-4 w-4" />
        </button>
      </div>
    </nav>

    {#key currentTxId}
      <article
        aria-label="Current transaction"
        class="border-base-300 bg-base-100 rounded-box min-w-0 border"
      >
        <div
          class="border-base-300 grid gap-2 border-b p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-4 sm:p-4"
        >
          <div class="min-w-0">
            <div
              class="text-base-content/70 mb-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs"
            >
              <span class="toolkit-data">Transaction #{currentTx.id}</span>
              <time class="toolkit-data" datetime={currentTx.date}>{currentTx.date}</time>
              <span>{currentTx.type}</span>
            </div>
            <h3 class="text-base leading-snug font-semibold wrap-anywhere sm:text-lg">
              {currentTx.description}
            </h3>
          </div>
          <div class="min-w-0 sm:text-right">
            <div class="toolkit-data text-xl font-semibold">
              {formatMoney(currentTx.amount, currentTx.currency_code, currentTx.currency_symbol)}
            </div>
            {#if currentTx.fx_amount !== null && currentTx.fx_amount !== undefined}
              <div class="toolkit-data text-base-content/70 mt-1 text-xs">
                FX: {formatMoney(currentTx.fx_amount, currentTx.fx_currency, currentTx.fx_currency)}
              </div>
            {/if}
          </div>
        </div>

        <section aria-label="Categorization" class="border-base-300 space-y-3 border-b p-3 sm:p-4">
          {#if updateError}
            <div class="alert alert-error py-2 text-sm" role="alert">
              <Icon src={icons.ExclamationTriangle} class="h-4 w-4" />
              <span class="min-w-0 wrap-anywhere">
                {updateError} Your transaction and selection are unchanged. Try again.
              </span>
            </div>
          {/if}
          <div class="flex flex-col gap-2 sm:flex-row sm:items-end">
            <div class="min-w-0 flex-1">
              <label for="transaction-category" class="mb-1 block text-xs font-medium">
                Category
              </label>
              <select
                id="transaction-category"
                class="select select-bordered w-full min-w-0"
                bind:value={selectedCategories[currentTxId]}
                disabled={busy}
              >
                <option value="" disabled>Pick a category</option>
                {#each data.categories as category}
                  <option value={String(category.id)}>{category.name}</option>
                {/each}
              </select>
            </div>
            <button
              class="btn btn-primary shrink-0"
              disabled={busy || !selectedCategories[currentTxId]}
              on:click={applyCurrent}
            >
              {#if applyingCategory}<span class="loading loading-spinner loading-xs"
                ></span>{:else}<Icon src={icons.CheckCircle} class="h-4 w-4" />{/if}
              Apply category
            </button>
          </div>
          {#if selectedCategoryName}
            <p class="text-base-content/70 text-xs wrap-anywhere">
              Selected: {selectedCategoryName}
            </p>
          {/if}
          {#if currentCategoryName}
            <p class="text-base-content/70 text-xs wrap-anywhere">
              Current category: {currentCategoryName}
            </p>
          {/if}
          <div class="flex flex-wrap items-center gap-2">
            <button
              class="btn btn-ghost btn-sm"
              disabled={busy}
              on:click={() => tagCurrentTx('action_req')}
            >
              <Icon src={icons.ExclamationTriangle} class="h-4 w-4" /> Action Required
            </button>
            <button
              class="btn btn-ghost btn-sm"
              disabled={busy}
              on:click={() => tagCurrentTx('rule_potential')}
            >
              <Icon src={icons.Sparkles} class="h-4 w-4" /> Rule Potential
            </button>
          </div>
        </section>

        <section
          aria-label="Category suggestions"
          class="border-base-300 border-b px-3 py-3 sm:px-4"
        >
          <h4 class="text-xs font-semibold">Category suggestions</h4>
          {#if categorySuggestionsLoading}
            <p class="text-base-content/70 mt-2 text-xs" role="status">Loading suggestions…</p>
          {:else if categorySuggestionsError}
            <div class="mt-2 flex flex-wrap items-center gap-2 text-xs" role="alert">
              <span class="text-error min-w-0 wrap-anywhere">{categorySuggestionsError}</span>
              <button
                class="btn btn-ghost btn-xs"
                disabled={busy}
                on:click={() => loadCategorySuggestions(currentTx.id)}
              >
                Retry suggestions
              </button>
            </div>
          {:else if displayedCategorySuggestions.length === 0}
            <p class="text-base-content/70 mt-2 text-xs">No category suggestions yet</p>
          {:else}
            <ul class="divide-base-300 mt-1 divide-y">
              {#each displayedCategorySuggestions as suggestion}
                <li class="flex items-start gap-3 py-2">
                  <div class="min-w-0 flex-1">
                    <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                      <span class="font-medium wrap-anywhere">{suggestion.category_name}</span>
                      <span class="toolkit-data text-base-content/70 text-xs">
                        {formatSuggestionConfidence(suggestion.score)}
                      </span>
                      {#if isCurrentCategorySuggestion(suggestion)}<span
                          class="badge badge-outline badge-sm"
                        >
                          Current
                        </span>{/if}
                    </div>
                    <p class="text-base-content/70 mt-0.5 text-xs wrap-anywhere">
                      {suggestion.reason}
                    </p>
                  </div>
                  <button
                    class="btn btn-outline btn-sm shrink-0"
                    disabled={busy}
                    aria-label={`Apply suggestion: ${suggestion.category_name}`}
                    on:click={() => applySuggestedCategory(suggestion)}
                  >
                    Apply
                  </button>
                </li>
              {/each}
            </ul>
          {/if}
        </section>

        <section aria-label="Transaction details" class="space-y-3 p-3 text-sm sm:p-4">
          {#if currentTx.source_account || currentTx.destination_account}
            <div class="grid min-w-0 gap-3 sm:grid-cols-2">
              {#each [{ label: 'Source', account: currentTx.source_account }, { label: 'Destination', account: currentTx.destination_account }] as item}
                <details class="min-w-0">
                  <summary class="cursor-pointer text-sm wrap-anywhere">
                    <span class="text-base-content/70 text-xs">{item.label}</span> · {item.account
                      ?.name || (item.account ? `Account #${item.account.id}` : 'Not provided')}
                  </summary>
                  {#if item.account}
                    <dl class="text-base-content/70 mt-2 space-y-1 text-xs">
                      <div>
                        <dt class="inline">Account ID:</dt>
                        <dd class="toolkit-data inline">{item.account.id}</dd>
                      </div>
                      <div>
                        <dt class="inline">Type:</dt>
                        <dd class="inline">{item.account.type}</dd>
                      </div>
                      <div>
                        <dt class="inline">IBAN:</dt>
                        <dd class="toolkit-data inline">{item.account.iban || 'Not provided'}</dd>
                      </div>
                    </dl>
                  {/if}
                </details>
              {/each}
            </div>
          {/if}
          {#if currentTx.tags?.length}
            <div class="flex flex-wrap items-center gap-1.5" aria-label="Transaction tags">
              <span class="text-base-content/70 mr-1 text-xs">Tags</span>
              {#each currentTx.tags as tag}
                <span
                  class={`badge badge-sm h-auto min-h-5 max-w-full py-0.5 wrap-anywhere whitespace-normal ${isPrimary(tag) ? 'badge-primary' : 'badge-outline'}`}
                >
                  {tag}
                </span>
              {/each}
            </div>
          {/if}
          {#if splitNotesLines(currentTx.notes).length > 0}
            <details>
              <summary class="cursor-pointer text-xs font-medium">
                Notes ({splitNotesLines(currentTx.notes).length})
              </summary>
              <div class="text-base-content/70 mt-2 space-y-1 text-sm wrap-anywhere">
                {#each splitNotesLines(currentTx.notes) as line}<p>{line}</p>{/each}
              </div>
            </details>
          {/if}
        </section>
      </article>
    {/key}
  {/if}
</div>
