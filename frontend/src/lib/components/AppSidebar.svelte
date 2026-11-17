<script lang="ts">
  import { resolve } from '$app/paths';
  import { Icon } from '@steeze-ui/svelte-icon';
  import * as icons from '@steeze-ui/heroicons';

  export let onCloseMobile: () => void = () => {};
  export let isSuperuser = false;
</script>

<!-- SIDEBAR -->
<div class="drawer-side z-30">
  <label for="app-drawer" class="drawer-overlay !z-0"></label>

  <aside class="bg-base-100 text-base-content min-h-full w-80">
    <div class="flex h-full flex-col p-4">
      <div class="border-base-300 relative overflow-hidden rounded-sm border p-4">
        <button
          class="btn btn-ghost btn-circle absolute top-3 right-3 lg:hidden"
          on:click={onCloseMobile}
        >
          ✕
        </button>

        <a href={resolve('')} class="flex items-center gap-3">
          <img class="mask mask-squircle w-12" src="/logo_b.png" alt="Firefly Toolkit" />
          <div>
            <!-- <div class="text-base-content/55 text-[11px] tracking-[0.22em] uppercase">Workspace</div> -->
            <div class="text-xl font-semibold tracking-tight">Firefly Toolkit</div>
          </div>
        </a>
      </div>

      <ul class="menu mt-4 flex-1 gap-3 p-0">
        <li>
          <details open class="bg-base-100 rounded-sm p-2">
            <summary
              class="hover:bg-base-200/80 flex cursor-pointer items-center gap-3 rounded-sm px-3 py-3 font-medium"
            >
              <span class="toolkit-icon">
                <Icon src={icons.ShoppingBag} class="h-5 w-5" />
              </span>
              Transactions
            </summary>

            <ul class="mt-2 space-y-1">
              <li>
                <a href={resolve('tx/categorize')} class="rounded-sm">
                  <Icon src={icons.DocumentMagnifyingGlass} class="h-5 w-5" />
                  Categorize
                </a>
              </li>
              <li>
                <a href={resolve('tx/stats')} class="rounded-sm">
                  <Icon src={icons.ChartBar} class="h-5 w-5" />
                  Stats
                </a>
              </li>
            </ul>
          </details>
        </li>

        <li>
          <details open class="bg-base-100 rounded-sm p-2">
            <summary
              class="hover:bg-base-200/80 flex cursor-pointer items-center gap-3 rounded-sm px-3 py-3 font-medium"
            >
              <span class="toolkit-icon">
                <Icon src={icons.ArchiveBoxArrowDown} class="h-5 w-5" />
              </span>
              Import Tools
            </summary>

            <ul class="mt-2 space-y-1">
              <li>
                <a href={resolve('tools/velobank')} class="rounded-sm">
                  <Icon src={icons.DocumentArrowUp} class="h-5 w-5" />
                  VeloBank Import
                </a>
              </li>
            </ul>
          </details>
        </li>

        <li>
          <details open class="bg-base-100 rounded-sm p-2">
            <summary
              class="hover:bg-base-200/80 flex cursor-pointer items-center gap-3 rounded-sm px-3 py-3 font-medium"
            >
              <span class="toolkit-icon">
                <Icon src={icons.DocumentCurrencyEuro} class="h-5 w-5" />
              </span>
              BLIK Sync
            </summary>

            <ul class="mt-2 space-y-1">
              <li>
                <a href={resolve('blik/upload')} class="rounded-sm" data-sveltekit-reload="true">
                  <Icon src={icons.InboxArrowDown} class="h-5 w-5" />
                  File Upload
                </a>
              </li>
              <li>
                <a href={resolve('blik/file')} class="rounded-sm" data-sveltekit-reload="true">
                  <Icon src={icons.DocumentCurrencyEuro} class="h-5 w-5" />
                  File Preview
                </a>
              </li>
              <li>
                <a href={resolve('blik/file')} class="rounded-sm" data-sveltekit-reload="true">
                  <Icon src={icons.Bolt} class="h-5 w-5" />
                  Match & Update
                </a>
              </li>
              <li>
                <a href={resolve('blik/stats')} class="rounded-sm">
                  <Icon src={icons.ChartPie} class="h-5 w-5" />
                  Stats
                </a>
              </li>
            </ul>
          </details>
        </li>

        <li>
          <details open class="bg-base-100 rounded-sm p-2">
            <summary
              class="hover:bg-base-200/80 flex cursor-pointer items-center gap-3 rounded-sm px-3 py-3 font-medium"
            >
              <span class="toolkit-icon">
                <Icon src={icons.BuildingStorefront} class="h-5 w-5" />
              </span>
              Allegro
            </summary>

            <ul class="mt-2 space-y-1">
              <li>
                <a href={resolve('allegro/accounts')} class="rounded-sm">
                  <Icon src={icons.Key} class="h-5 w-5" />
                  Accounts
                </a>
              </li>
              <li>
                <a href={resolve('allegro/stats')} class="rounded-sm">
                  <Icon src={icons.ChartBar} class="h-5 w-5" />
                  Stats
                </a>
              </li>
            </ul>
          </details>
        </li>

        {#if isSuperuser}
          <li>
            <details open class="bg-base-100 rounded-sm p-2">
              <summary
                class="hover:bg-base-200/80 flex cursor-pointer items-center gap-3 rounded-sm px-3 py-3 font-medium"
              >
                <span class="toolkit-icon">
                  <Icon src={icons.ShieldCheck} class="h-5 w-5" />
                </span>
                Administration
              </summary>

              <ul class="mt-2 space-y-1">
                <li>
                  <a href={resolve('users')} class="rounded-sm">
                    <Icon src={icons.Users} class="h-5 w-5" />
                    Users
                  </a>
                </li>
                <li>
                  <a href={resolve('audit-log')} class="rounded-sm">
                    <Icon src={icons.ClipboardDocumentList} class="h-5 w-5" />
                    Audit log
                  </a>
                </li>
              </ul>
            </details>
          </li>
        {/if}
      </ul>
    </div>
  </aside>
</div>
