<script lang="ts">
  import { pendingGroupIds, type ReconciliationGroup } from '#lib/api/reconciliation.js';

  export let groups: ReconciliationGroup[];
  export let busy: boolean;
  export let selectedGroups: Set<string>;
  export let onToggle: (id: string, checked: boolean) => void;

  let filter = 'review';
  $: review = groups.filter(
    (group) =>
      group.bank.length !== group.firefly.length ||
      group.contains_transfer ||
      group.firefly.some((line) => !line.can_mark && line.reconciled !== true)
  );
  $: ready = groups.filter((group) => pendingGroupIds(group).length);
  $: visible = filter === 'review' ? review : filter === 'ready' ? ready : groups;
</script>

<section class="card bg-base-100 min-w-0 gap-3 p-4" aria-label="Uzgadnianie grup operacji">
  <h2 class="font-semibold">Uzgadnianie grup operacji</h2>
  <label class="text-sm">
    Pokaż grupy
    <select class="select select-sm ml-1" bind:value={filter}>
      <option value="review">Do sprawdzenia ({review.length})</option>
      <option value="ready">Gotowe do uzgodnienia ({ready.length})</option>
      <option value="all">Wszystkie ({groups.length})</option>
    </select>
  </label>
  <ul class="space-y-2">
    {#each visible as group (group.id)}
      {@const difference = group.bank.length - group.firefly.length}
      {@const pending = pendingGroupIds(group).length}
      <li class="border-base-300 rounded border p-2">
        <div class="flex min-w-0 items-start gap-2">
          {#if pending}
            <input
              type="checkbox"
              class="checkbox checkbox-sm"
              aria-label={`Uzgodnij grupę ${group.date} ${group.amount} ${group.currency}`}
              checked={selectedGroups.has(group.id)}
              disabled={busy}
              on:change={(event) => onToggle(group.id, event.currentTarget.checked)}
            />
          {/if}
          <details class="min-w-0 grow">
            <summary class="cursor-pointer text-sm">
              <strong class="wrap-anywhere">{group.date} · {group.amount} {group.currency}</strong>
              <span class="block">
                Bank: {group.bank.length} · Firefly: {group.firefly.length} ·
                {#if difference > 0}
                  Brakujące w Firefly ({difference})
                {:else if difference < 0}
                  Nadmiarowe w Firefly ({-difference})
                {:else}
                  Zgodna liczba i kwota
                {/if}
              </span>
              <span class="text-base-content/70 block text-xs">
                Dokładna zgodność opisów ({group.exact_description_count})
                {#if group.contains_transfer}
                  · Transfer w grupie — do przeglądu
                {:else if !difference && !group.eligible_ids.length}
                  · Brak transakcji do oznaczenia
                {:else if !difference && !pending}
                  · Kwalifikujące się wpisy są już uzgodnione
                {/if}
              </span>
            </summary>
            <div class="mt-2 grid min-w-0 gap-2 sm:grid-cols-2">
              <div>
                <p class="text-sm font-medium">Bank</p>
                {#each group.bank as line (line.id)}
                  <p class="text-sm wrap-anywhere whitespace-pre-wrap">{line.description}</p>
                  <p class="text-base-content/70 text-xs">Wiersz {line.id}</p>
                {/each}
              </div>
              <div>
                <p class="text-sm font-medium">Firefly</p>
                {#each group.firefly as line (line.id)}
                  <p class="text-sm wrap-anywhere whitespace-pre-wrap">{line.description}</p>
                  <p class="text-base-content/70 text-xs">
                    #{line.id}{#if line.restriction}
                      · {line.restriction}{:else if line.reconciled}
                      · Już uzgodniona
                    {/if}
                  </p>
                {/each}
              </div>
            </div>
          </details>
        </div>
      </li>
    {:else}
      <li class="text-base-content/70 text-sm">
        {filter === 'review' ? 'Brak grup do sprawdzenia.' : 'Brak gotowych grup.'}
      </li>
    {/each}
  </ul>
</section>
