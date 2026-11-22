<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { Icon } from '@steeze-ui/svelte-icon';
  import * as icons from '@steeze-ui/heroicons';

  let {
    onCloseMobile = () => {},
    isSuperuser = false,
    username,
    version = null
  }: {
    onCloseMobile?: () => void;
    isSuperuser?: boolean;
    username?: string;
    version?: string | null;
  } = $props();

  type NavigationSection = {
    label: string;
    icon: typeof icons.ShoppingBag;
    admin?: boolean;
    links: {
      label: string;
      href: string;
      icon: typeof icons.Key;
      reload?: boolean;
      alias?: boolean;
    }[];
  };

  const sections: NavigationSection[] = [
    {
      label: 'Transactions',
      icon: icons.ShoppingBag,
      links: [
        {
          label: 'Categorize',
          href: resolve('tx/categorize'),
          icon: icons.DocumentMagnifyingGlass
        },
        { label: 'Stats', href: resolve('tx/stats'), icon: icons.ChartBar },
        {
          label: 'Reconciliation',
          href: resolve('tx/reconcile'),
          icon: icons.DocumentMagnifyingGlass
        },
        {
          label: 'Potential duplicates',
          href: resolve('tx/duplicates'),
          icon: icons.DocumentMagnifyingGlass
        }
      ]
    },
    {
      label: 'Import Tools',
      icon: icons.ArchiveBoxArrowDown,
      links: [
        { label: 'VeloBank Import', href: resolve('tools/velobank'), icon: icons.DocumentArrowUp }
      ]
    },
    {
      label: 'BLIK Sync',
      icon: icons.DocumentCurrencyEuro,
      links: [
        {
          label: 'File Upload',
          href: resolve('blik/upload'),
          icon: icons.InboxArrowDown,
          reload: true
        },
        {
          label: 'File Preview',
          href: resolve('blik/file'),
          icon: icons.DocumentCurrencyEuro,
          reload: true
        },
        {
          label: 'Match & Update',
          href: resolve('blik/file'),
          icon: icons.Bolt,
          reload: true,
          alias: true
        },
        { label: 'Stats', href: resolve('blik/stats'), icon: icons.ChartPie }
      ]
    },
    {
      label: 'Allegro',
      icon: icons.BuildingStorefront,
      links: [
        { label: 'Accounts', href: resolve('allegro/accounts'), icon: icons.Key },
        { label: 'Stats', href: resolve('allegro/stats'), icon: icons.ChartBar }
      ]
    },
    {
      label: 'Administration',
      icon: icons.ShieldCheck,
      admin: true,
      links: [
        { label: 'Users', href: resolve('users'), icon: icons.Users },
        { label: 'Audit log', href: resolve('audit-log'), icon: icons.ClipboardDocumentList }
      ]
    }
  ];

  function isActive(href: string) {
    if (href === resolve('allegro/accounts') && page.url.pathname.startsWith('/allegro/')) {
      return !page.url.pathname.startsWith('/allegro/stats');
    }
    return page.url.pathname === href || page.url.pathname.startsWith(`${href}/`);
  }
</script>

<div class="drawer-side z-30">
  <button
    type="button"
    class="drawer-overlay !z-0 lg:hidden"
    aria-label="Close navigation backdrop"
    tabindex="-1"
    onclick={onCloseMobile}
  ></button>
  <aside
    id="app-navigation"
    class="bg-base-100 border-base-300 text-base-content flex h-dvh w-[232px] max-w-[85vw] flex-col border-r"
  >
    <div
      class="border-base-300 flex h-14 shrink-0 items-center justify-between gap-2 border-b px-3"
    >
      <a
        href={resolve('')}
        class="flex min-w-0 items-center gap-2 text-sm font-semibold"
        onclick={onCloseMobile}
      >
        <img class="h-7 w-7 shrink-0" src="/logo_b.png" alt="" />
        <span class="truncate">Firefly Toolkit</span>
      </a>
      <button
        id="close-navigation"
        class="btn btn-ghost btn-sm btn-square lg:hidden"
        aria-label="Close navigation"
        onclick={onCloseMobile}
      >
        <Icon src={icons.XMark} class="h-4 w-4" />
      </button>
    </div>

    <nav
      aria-label="Main navigation"
      class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 py-2"
    >
      <ul class="shell-menu space-y-1">
        {#each sections as section}
          {#if !section.admin || isSuperuser}
            <li>
              <details open>
                <summary
                  class="hover:bg-base-200 flex min-h-9 cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-xs font-semibold"
                >
                  <Icon src={section.icon} class="h-4 w-4 shrink-0" />
                  <span class="min-w-0 flex-1">{section.label}</span>
                  <Icon
                    src={icons.ChevronDown}
                    class="section-chevron h-3 w-3 shrink-0 opacity-60"
                  />
                </summary>
                <ul class="mt-0.5 space-y-0.5">
                  {#each section.links as link}
                    <li>
                      <!-- href values are resolved in the section definitions above. -->
                      <!-- eslint-disable svelte/no-navigation-without-resolve -->
                      <a
                        href={link.href}
                        aria-current={!link.alias && isActive(link.href) ? 'page' : undefined}
                        data-sveltekit-reload={link.reload ? true : undefined}
                        onclick={onCloseMobile}
                        class="hover:bg-base-200 flex min-h-9 items-center gap-2 rounded-sm border-l-2 border-transparent py-1.5 pr-2 pl-5 text-sm"
                      >
                        <Icon src={link.icon} class="h-4 w-4 shrink-0" />
                        <span class="min-w-0 wrap-anywhere">{link.label}</span>
                      </a>
                      <!-- eslint-enable svelte/no-navigation-without-resolve -->
                    </li>
                  {/each}
                </ul>
              </details>
            </li>
          {/if}
        {/each}
      </ul>
    </nav>

    <div class="border-base-300 text-base-content/70 shrink-0 space-y-1 border-t px-3 py-2 text-xs">
      <div class="truncate" title={username}>Session: {username ?? 'Loading…'}</div>
      <div class="toolkit-data">Toolkit · {version ? `v${version}` : 'Version unavailable'}</div>
    </div>
  </aside>
</div>

<style>
  summary {
    list-style: none;
  }
  summary::-webkit-details-marker {
    display: none;
  }
  details:not([open]) :global(.section-chevron) {
    transform: rotate(-90deg);
  }
  .shell-menu a[aria-current='page'] {
    background: var(--color-base-200);
    border-left-color: var(--color-primary);
    font-weight: 600;
  }
  .shell-menu :is(a, summary):focus-visible {
    outline: 2px solid var(--color-primary);
    outline-offset: -2px;
  }
</style>
