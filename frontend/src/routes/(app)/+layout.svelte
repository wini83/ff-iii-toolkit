<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { afterNavigate, goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';

  import { Icon } from '@steeze-ui/svelte-icon';
  import * as icons from '@steeze-ui/heroicons';

  import AppSidebar from '#lib/components/AppSidebar.svelte';
  import { getVersion } from '#lib/api/system.js';
  import { getMe } from '#lib/api/me.js';
  import { userSecrets } from '#lib/api/user_secrets.js';
  import type { components } from '#lib/api/schema.js';

  type MeUser = Awaited<ReturnType<typeof getMe>>;
  type Toast = { id: string; type: 'success' | 'error' | 'info' | 'warning'; msg: string };
  type ToastEventDetail = Pick<Toast, 'type' | 'msg'>;
  type VaultStatus = components['schemas']['VaultStatusResponse'];

  const DEFAULT_APP_TITLE = 'Firefly Toolkit';
  const THEME_STORAGE_KEY = 'ff-toolkit-theme';
  // prosty state dla drawer
  let drawerOpen = false;
  let navigationButton: HTMLButtonElement;
  let appVersion: string | null = null;
  let vaultMenu: HTMLDivElement;
  let vaultOpen = false;
  let meUser: MeUser | null = null;
  let dynamicTitle = resolveRouteTitle(page.url.pathname);
  let headTitle =
    dynamicTitle === DEFAULT_APP_TITLE
      ? DEFAULT_APP_TITLE
      : `${dynamicTitle} — ${DEFAULT_APP_TITLE}`;
  let theme: 'wireframe' | 'black' = 'wireframe';
  let themeReady = false;
  let vaultStatus: VaultStatus | null = null;
  let vaultLoading = false;
  let vaultUnlocking = false;
  let vaultLocking = false;
  let vaultPassphrase = '';

  // toasty trzymamy jako prosta tablica
  let toasts: Toast[] = [];

  const toastIcons = {
    success: icons.CheckCircle,
    error: icons.XCircle,
    info: icons.InformationCircle,
    warning: icons.ExclamationTriangle
  } as const;

  function resolveRouteTitle(pathname: string): string {
    if (pathname.startsWith('/tx/categorize')) return 'Transactions / Categorize';
    if (pathname.startsWith('/tx/stats')) return 'Transactions / Stats';
    if (pathname.startsWith('/allegro/accounts')) return 'Allegro / Accounts';
    if (pathname.startsWith('/allegro/') && pathname.endsWith('/matches'))
      return 'Allegro / Matches';
    if (pathname.startsWith('/allegro/') && pathname.endsWith('/payments'))
      return 'Allegro / Payments';
    if (pathname.startsWith('/allegro/stats')) return 'Allegro / Stats';
    if (pathname.startsWith('/blik/upload')) return 'BLIK Sync / File Upload';
    if (pathname.startsWith('/blik/file')) return 'BLIK Sync / File Preview';
    if (pathname.startsWith('/blik/stats')) return 'BLIK Sync / Stats';
    if (pathname.startsWith('/tools/velobank')) return 'Import Tools / VeloBank Import';
    if (pathname.startsWith('/profile')) return 'Profile';
    if (pathname.startsWith('/settings/secrets')) return 'Settings / Secrets';
    if (pathname.startsWith('/audit-log')) return 'Administration / Audit Log';
    if (pathname.startsWith('/users')) return 'Administration / Users';
    return DEFAULT_APP_TITLE;
  }

  afterNavigate(() => {
    dynamicTitle = resolveRouteTitle(page.url.pathname);
    headTitle =
      dynamicTitle === DEFAULT_APP_TITLE
        ? DEFAULT_APP_TITLE
        : `${dynamicTitle} — ${DEFAULT_APP_TITLE}`;
  });

  function applyTheme(nextTheme: 'wireframe' | 'black') {
    theme = nextTheme;
    document.documentElement.setAttribute('data-theme', nextTheme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    } catch {
      // Keep the switch usable even when browser storage is unavailable.
    }
  }

  function toggleTheme() {
    applyTheme(theme === 'wireframe' ? 'black' : 'wireframe');
  }

  function emitToast(type: Toast['type'], msg: string) {
    window.dispatchEvent(new CustomEvent('toast', { detail: { type, msg } }));
  }

  function getErrorMessage(error: unknown, fallback: string) {
    if (error instanceof Error && error.message.trim()) return error.message;
    return fallback;
  }

  function formatDateTime(timestamp: string | null | undefined) {
    if (!timestamp) return null;

    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return timestamp;

    return new Intl.DateTimeFormat('pl-PL', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    }).format(date);
  }

  function getVaultExpiryText(status: VaultStatus | null) {
    const expiry = formatDateTime(status?.expires_at);
    return expiry ? `Vault session expires at ${expiry}` : null;
  }

  function getVaultStatusMeta(status: VaultStatus | null) {
    if (!status) {
      return {
        label: 'Vault unavailable',
        icon: icons.ShieldExclamation,
        badgeClass: 'badge-ghost',
        buttonClass: 'btn-ghost'
      };
    }

    if (!status.configured) {
      return {
        label: 'Vault setup required',
        icon: icons.ShieldCheck,
        badgeClass: 'badge-info',
        buttonClass: 'btn-ghost'
      };
    }

    if (!status.unlocked) {
      return {
        label: 'Vault locked',
        icon: icons.LockClosed,
        badgeClass: 'badge-warning',
        buttonClass: 'btn-ghost'
      };
    }

    return {
      label: 'Vault unlocked',
      icon: icons.LockOpen,
      badgeClass: 'badge-success',
      buttonClass: 'btn-ghost'
    };
  }

  async function refreshVaultStatus(showToast = false) {
    vaultLoading = true;

    try {
      vaultStatus = await userSecrets.getVaultStatus();
    } catch (error) {
      vaultStatus = null;
      if (showToast) {
        emitToast('error', getErrorMessage(error, 'Failed to load vault status'));
      }
    } finally {
      vaultLoading = false;
    }
  }

  async function unlockVaultFromLayout() {
    if (!vaultPassphrase.trim()) return;

    vaultUnlocking = true;

    try {
      const status = await userSecrets.unlockVault({ passphrase: vaultPassphrase });
      vaultStatus = status;
      vaultPassphrase = '';
    } catch (error) {
      emitToast('error', getErrorMessage(error, 'Failed to unlock vault'));
    } finally {
      vaultUnlocking = false;
    }
  }

  async function lockVaultFromLayout() {
    vaultLocking = true;

    try {
      vaultStatus = await userSecrets.lockVault();
    } catch (error) {
      emitToast('error', getErrorMessage(error, 'Failed to lock vault'));
    } finally {
      vaultLocking = false;
    }
  }

  function openSecretsSettings() {
    vaultOpen = false;
    void goto(resolve('settings/secrets'));
  }

  async function logout() {
    try {
      await fetch('/logout', { method: 'POST' });
    } finally {
      meUser = null;
      await goto(resolve('login'), { invalidateAll: true });
    }
  }

  async function openDrawerOnMobile() {
    drawerOpen = true;
    await tick();
    const drawer = document.querySelector('.drawer-side');
    await Promise.allSettled(drawer?.getAnimations().map((animation) => animation.finished) ?? []);
    if (drawerOpen) document.getElementById('close-navigation')?.focus();
  }

  async function closeDrawerOnMobile() {
    if (!drawerOpen) return;
    drawerOpen = false;
    await tick();
    navigationButton?.focus();
  }

  function handleDrawerKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape' && vaultOpen) {
      vaultOpen = false;
      vaultMenu.querySelector('button')?.focus();
    }
    if (!drawerOpen) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeDrawerOnMobile();
    }
    if (event.key !== 'Tab') return;
    const items = Array.from(
      document.querySelectorAll<HTMLElement>(
        '#app-navigation a, #app-navigation button, #app-navigation summary'
      )
    ).filter((item) => item.getClientRects().length > 0);
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }

  async function loadVersion() {
    try {
      appVersion = (await getVersion()).version;
    } catch {
      appVersion = null;
    }
  }

  function isToastEventDetail(detail: unknown): detail is ToastEventDetail {
    if (!detail || typeof detail !== 'object') return false;

    const candidate = detail as Partial<ToastEventDetail>;
    const validType =
      candidate.type === 'success' ||
      candidate.type === 'error' ||
      candidate.type === 'info' ||
      candidate.type === 'warning';

    return validType && typeof candidate.msg === 'string' && candidate.msg.length > 0;
  }

  function handleToastEvent(event: Event) {
    const detail = (event as CustomEvent<unknown>).detail;

    if (!isToastEventDetail(detail)) {
      console.warn('Ignored toast event (missing detail):', event);
      return;
    }

    const id = crypto?.randomUUID?.() ?? String(Date.now());
    toasts = [...toasts, { id, type: detail.type, msg: detail.msg }];

    setTimeout(() => {
      toasts = toasts.filter((t) => t.id !== id);
    }, 3000);
  }

  async function loadCurrentUser() {
    try {
      meUser = await getMe();
    } catch (error) {
      console.warn('Failed to fetch current user via /api/me:', error);
    }
  }

  onMount(() => {
    // app.html resolves the saved/system preference before the first paint.
    theme = document.documentElement.dataset.theme === 'black' ? 'black' : 'wireframe';
    themeReady = true;
    void loadCurrentUser();
    void loadVersion();
    void refreshVaultStatus();

    const listener = (event: Event) => handleToastEvent(event);
    const vaultStatusListener = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;

      if (
        detail &&
        typeof detail === 'object' &&
        'configured' in detail &&
        'unlocked' in detail &&
        typeof detail.configured === 'boolean' &&
        typeof detail.unlocked === 'boolean'
      ) {
        vaultStatus = detail as VaultStatus;
      }
    };

    window.addEventListener('toast', listener as EventListener);
    window.addEventListener('vault-status-changed', vaultStatusListener as EventListener);

    return () => {
      window.removeEventListener('toast', listener as EventListener);
      window.removeEventListener('vault-status-changed', vaultStatusListener as EventListener);
    };
  });
</script>

<svelte:head>
  <title>{headTitle}</title>
</svelte:head>

<svelte:window
  on:keydown={handleDrawerKeydown}
  on:pointerdown={(event) => {
    if (vaultOpen && event.target instanceof Node && !vaultMenu.contains(event.target))
      vaultOpen = false;
  }}
  on:resize={() => {
    if (window.innerWidth >= 1024 && drawerOpen) closeDrawerOnMobile();
  }}
/>

<div class="drawer bg-base-200 text-base-content lg:drawer-open h-dvh overflow-x-hidden">
  <input
    id="app-drawer"
    type="checkbox"
    class="drawer-toggle"
    tabindex="-1"
    aria-hidden="true"
    bind:checked={drawerOpen}
  />

  <!-- CONTENT -->
  <div
    inert={drawerOpen}
    class="drawer-content flex min-w-0 flex-col overflow-x-hidden overflow-y-auto"
  >
    <!-- NAVBAR -->
    <div class="bg-base-100 border-base-300 sticky top-0 z-10 border-b">
      <div class="navbar h-14 min-h-0 gap-2 px-3 sm:px-4">
        <div class="flex min-w-0 flex-1 items-center gap-2">
          <button
            bind:this={navigationButton}
            type="button"
            class="btn btn-ghost btn-sm btn-square lg:hidden"
            disabled={!themeReady}
            aria-label="Open navigation"
            aria-controls="app-navigation"
            aria-expanded={drawerOpen}
            on:click={openDrawerOnMobile}
          >
            <Icon src={icons.Bars3} class="h-5 w-5" />
          </button>
          <h1 class="min-w-0 truncate text-sm font-semibold sm:text-base" title={dynamicTitle}>
            {dynamicTitle}
          </h1>
        </div>

        <div class="flex shrink-0 items-center gap-0.5">
          <div
            bind:this={vaultMenu}
            class="dropdown dropdown-end"
            class:dropdown-open={vaultOpen}
            class:dropdown-close={!vaultOpen}
          >
            <button
              on:click={() => (vaultOpen = !vaultOpen)}
              aria-expanded={vaultOpen}
              aria-controls="vault-panel"
              class="btn btn-ghost btn-sm btn-square rounded-sm"
              disabled={!themeReady}
              aria-label="Vault status"
              title={getVaultStatusMeta(vaultStatus).label}
            >
              {#if vaultLoading}
                <span class="loading loading-spinner loading-sm"></span>
              {:else}
                <Icon src={getVaultStatusMeta(vaultStatus).icon} class="h-5 w-5" />
              {/if}
            </button>

            <div
              id="vault-panel"
              class="dropdown-content bg-base-100 rounded-box border-base-200 fixed top-14 right-3 z-20 mt-2 max-h-[calc(100dvh-4.5rem)] w-80 max-w-[calc(100vw-1.5rem)] overflow-y-auto border p-3 sm:absolute sm:top-auto sm:right-0"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <div class="text-sm font-semibold">Vault</div>
                  <div class="text-base-content/70 mt-1 text-xs">
                    {getVaultStatusMeta(vaultStatus).label}
                  </div>
                  {#if getVaultExpiryText(vaultStatus)}
                    <div class="text-base-content/60 mt-1 text-xs">
                      {getVaultExpiryText(vaultStatus)}
                    </div>
                  {/if}
                </div>

                <button
                  class="btn btn-ghost btn-xs"
                  disabled={vaultLoading}
                  on:click={() => refreshVaultStatus(true)}
                >
                  {#if vaultLoading}
                    <span class="loading loading-spinner loading-xs"></span>
                  {:else}
                    <Icon src={icons.ArrowPath} class="h-4 w-4" />
                  {/if}
                  Refresh
                </button>
              </div>

              {#if vaultStatus && !vaultStatus.configured}
                <div class="alert alert-info mt-4">
                  <Icon src={icons.InformationCircle} class="h-5 w-5" />
                  <span>Vault is not configured yet.</span>
                </div>

                <button class="btn btn-primary btn-sm mt-4 w-full" on:click={openSecretsSettings}>
                  <Icon src={icons.Key} class="h-4 w-4" />
                  Open vault settings
                </button>
              {:else if vaultStatus?.unlocked}
                <div class="mt-4 flex gap-2">
                  <button
                    class="btn btn-outline btn-sm flex-1"
                    disabled={vaultLocking}
                    on:click={lockVaultFromLayout}
                  >
                    {#if vaultLocking}
                      <span class="loading loading-spinner loading-sm"></span>
                    {:else}
                      <Icon src={icons.LockClosed} class="h-4 w-4" />
                    {/if}
                    Lock
                  </button>
                  <button class="btn btn-ghost btn-sm flex-1" on:click={openSecretsSettings}>
                    <Icon src={icons.Key} class="h-4 w-4" />
                    Manage
                  </button>
                </div>
              {:else}
                <fieldset class="fieldset mt-4">
                  <legend class="fieldset-legend">Passphrase</legend>
                  <input
                    class="input input-bordered w-full"
                    type="password"
                    bind:value={vaultPassphrase}
                    disabled={vaultUnlocking}
                    placeholder="Enter vault passphrase"
                  />
                </fieldset>

                <div class="mt-4 flex gap-2">
                  <button
                    class="btn btn-primary btn-sm flex-1"
                    disabled={vaultUnlocking || !vaultPassphrase.trim()}
                    on:click={unlockVaultFromLayout}
                  >
                    {#if vaultUnlocking}
                      <span class="loading loading-spinner loading-sm"></span>
                    {:else}
                      <Icon src={icons.LockOpen} class="h-4 w-4" />
                    {/if}
                    Unlock
                  </button>
                  <button class="btn btn-ghost btn-sm flex-1" on:click={openSecretsSettings}>
                    <Icon src={icons.Key} class="h-4 w-4" />
                    Open settings
                  </button>
                </div>
              {/if}
            </div>
          </div>

          <button
            class="btn btn-ghost btn-sm btn-square rounded-sm"
            on:click={toggleTheme}
            disabled={!themeReady}
            aria-label={`Switch to ${theme === 'wireframe' ? 'dark' : 'light'} theme`}
            title={theme === 'wireframe' ? 'Switch to dark mode' : 'Switch to light mode'}
          >
            <Icon src={theme === 'wireframe' ? icons.Moon : icons.Sun} class="h-5 w-5" />
          </button>

          <div class="dropdown dropdown-end">
            <div
              class="tooltip tooltip-bottom"
              data-tip={meUser ? `Signed in as ${meUser.username}` : undefined}
            >
              <button
                tabindex="0"
                class="btn btn-ghost btn-sm rounded-sm px-2"
                aria-label="Account menu"
              >
                <span
                  class="bg-base-200 flex h-6 w-6 items-center justify-center rounded-sm text-xs font-semibold"
                  aria-hidden="true"
                >
                  {meUser?.username?.slice(0, 2).toUpperCase() ?? '…'}
                </span>
                <Icon src={icons.ChevronDown} class="h-3 w-3 opacity-60" />
              </button>
            </div>
            <ul
              class="menu menu-sm dropdown-content bg-base-100 rounded-box border-base-200 mt-3 w-56 border p-2"
            >
              <li>
                <a href={resolve('profile')}><Icon src={icons.User} class="h-5 w-5" /> Profile</a>
              </li>
              <li>
                <a href={resolve('settings/secrets')}>
                  <Icon src={icons.Key} class="h-5 w-5" /> Secrets
                </a>
              </li>
              <li>
                <button on:click={logout}>
                  <Icon src={icons.Power} class="h-5 w-5" />Log out
                </button>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>

    <!-- PAGE CONTENT -->
    <main id="main-content" class="min-w-0 flex-1 px-3 py-4 sm:px-4">
      <div class="mx-auto w-full max-w-7xl">
        <slot />
      </div>
    </main>
  </div>

  <AppSidebar
    onCloseMobile={closeDrawerOnMobile}
    isSuperuser={meUser?.is_superuser ?? false}
    username={meUser?.username}
    version={appVersion}
  />
</div>

<!-- TOASTS -->
<div class="toast toast-top toast-end pointer-events-none z-50">
  {#each toasts as t (t.id)}
    <div
      class={'alert pointer-events-auto  ' +
        (t.type === 'success'
          ? 'alert-success'
          : t.type === 'error'
            ? 'alert-error'
            : t.type === 'info'
              ? 'alert-info'
              : 'alert-warning')}
    >
      <Icon src={toastIcons[t.type]} class="h-5 w-5 shrink-0" />
      <span>{t.msg}</span>
    </div>
  {/each}
</div>
