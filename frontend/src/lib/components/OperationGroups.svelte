<script lang="ts">
  import type { components } from '#lib/api/schema.js';

  type Group = components['schemas']['OperationGroup'];
  export let groups: Group[];

  const reasonLabels: Record<Group['reason'], string> = {
    exact: 'Zgodne konkretne opisy',
    card_prefix: 'Opis Firefly po prefiksie karty jest początkiem opisu bankowego',
    overlap: 'Nakładający się kandydaci lub różne punkty sprzedaży',
    date_shift: 'Kandydaci z przesunięciem daty · Do sprawdzenia',
    generic_blik: 'Ogólny opis BLIK wymaga sprawdzenia',
    different_descriptions: 'Zgodny dzień i kwota, różne opisy',
    bank_only: 'Operacje widoczne tylko w banku',
    firefly_only: 'Operacje widoczne tylko w Firefly'
  };

  let filter = 'issues';
  $: missing = groups.reduce((total, group) => total + (group.missing_count ?? 0), 0);
  $: excess = groups.reduce((total, group) => total + (group.excess_count ?? 0), 0);
  $: shifted = groups.filter((group) => group.reason === 'date_shift').length;
  $: visible = groups
    .filter((group) => {
      const countable = group.reason === 'exact' || group.reason === 'card_prefix';
      if (filter === 'all') return true;
      if (filter === 'missing') return !!group.missing_count;
      if (filter === 'excess') return !!group.excess_count;
      if (filter === 'date_shift') return group.reason === 'date_shift';
      return !countable || group.bank.length !== 1 || group.firefly.length !== 1;
    })
    .sort((a, b) =>
      (b.bank[0]?.date ?? b.firefly[0].date).localeCompare(a.bank[0]?.date ?? a.firefly[0].date)
    );

  function amountLabel(amount: string, currency: string) {
    const value = amount
      .replace(/^-/, '')
      .replace(/(\.\d*?)0+$/, '$1')
      .replace(/\.$/, '');
    return `${value.replace('.', ',')} ${currency === 'PLN' ? 'zł' : currency}`;
  }
</script>

<section class="card bg-base-100 min-w-0 gap-3 p-4" aria-label="Bilans operacji w podglądzie">
  <h2 class="font-semibold">Bilans operacji · podgląd</h2>
  <p class="text-sm">
    Brakujące w Firefly ({missing}) · Nadmiarowe w Firefly ({excess}) · Przesunięcie daty ({shifted} grup)
  </p>
  <label class="text-sm">
    Grupy operacji
    <select class="select select-sm ml-1" bind:value={filter}>
      <option value="issues">Do sprawdzenia</option>
      <option value="missing">Brakujące w Firefly</option>
      <option value="excess">Nadmiarowe w Firefly</option>
      <option value="date_shift">Z przesunięciem daty</option>
      <option value="all">Wszystkie grupy</option>
    </select>
  </label>
  <ul class="space-y-2" aria-label="Grupy operacji">
    {#each visible as group (group.bank.length ? `bank-${group.bank[0].id}` : `firefly-${group.firefly[0].id}`)}
      <li class="border-base-300 rounded border p-2">
        <details>
          <summary class="cursor-pointer text-sm">
            <strong>
              Bank: {group.bank.length} × {amountLabel(group.amount, group.currency)} · Firefly: {group
                .firefly.length} × {amountLabel(group.amount, group.currency)}
            </strong>
            <span class="block">
              {#if group.missing_count}
                Brakujące w Firefly ({group.missing_count})
              {:else if group.excess_count}
                Nadmiarowe w Firefly ({group.excess_count}){group.bank.length
                  ? ' · Możliwy duplikat'
                  : ''}
              {:else if (group.reason === 'exact' || group.reason === 'card_prefix') && group.bank.length > 1}
                Zgodna liczba operacji · Niejednoznaczne pary
              {:else}
                {reasonLabels[group.reason]}
              {/if}
            </span>
          </summary>
          <p class="mt-2 text-sm">
            {reasonLabels[group.reason]} · {group.amount.startsWith('-') ? 'Obciążenie' : 'Wpływ'}
          </p>
          {#if group.compared_key}
            <p class="text-sm wrap-anywhere">Porównany klucz <code>{group.compared_key}</code></p>
          {/if}
          <div class="mt-2 grid min-w-0 gap-2 sm:grid-cols-2">
            <div>
              <p class="text-sm font-medium">Bank</p>
              {#each group.bank as entry (entry.id)}
                <p class="text-sm wrap-anywhere whitespace-pre-wrap">{entry.description}</p>
                <p class="text-base-content/70 text-xs">{entry.date} · wiersz {entry.id}</p>
              {/each}
            </div>
            <div>
              <p class="text-sm font-medium">Firefly</p>
              {#each group.firefly as entry (entry.id)}
                <p class="text-sm wrap-anywhere whitespace-pre-wrap">{entry.description}</p>
                <p class="text-base-content/70 text-xs">{entry.date} · #{entry.id}</p>
              {/each}
            </div>
          </div>
        </details>
      </li>
    {:else}
      <li class="text-base-content/70 text-sm">Brak grup w tym widoku.</li>
    {/each}
  </ul>
</section>
