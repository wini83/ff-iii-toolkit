<script lang="ts">
  import WorkflowHeader from '#lib/components/WorkflowHeader.svelte';
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { Icon } from '@steeze-ui/svelte-icon';
  import * as icons from '@steeze-ui/heroicons';

  import { allegro } from '#lib/api/allegro.js';
  import { userSecrets } from '#lib/api/user_secrets.js';
  import type { components } from '#lib/api/schema.js';

  type UserSecret = components['schemas']['UserSecretResponse'];
  type VaultStatus = components['schemas']['VaultStatusResponse'];

  let loading = true;
  let networkError: string | null = null;
  let secrets: UserSecret[] = [];
  let vaultStatus: VaultStatus | null = null;

  function getNonEmptyString(value: unknown): string | null {
    if (typeof value !== 'string') return null;
    const normalized = value.trim();
    return normalized.length ? normalized : null;
  }

  function extractErrorMessage(error: unknown, fallback: string) {
    const normalizedFallback = getNonEmptyString(fallback) ?? 'Unexpected error';
    const rawError = getNonEmptyString(error);
    if (rawError) return rawError;

    if (error && typeof error === 'object') {
      const err = error as { detail?: unknown; error?: unknown; message?: unknown };

      const message = getNonEmptyString(err.message);
      if (message) return message;

      const errText = getNonEmptyString(err.error);
      if (errText) return errText;

      if (Array.isArray(err.detail)) {
        const joined = err.detail
          .map((item) => {
            if (item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string') {
              return getNonEmptyString(item.msg);
            }
            return null;
          })
          .filter((item): item is string => item !== null)
          .join('; ');

        if (joined) return joined;
      }

      const detail = getNonEmptyString(err.detail);
      if (detail) return detail;
    }

    return normalizedFallback;
  }

  function emitToast(type: 'info' | 'success' | 'error', msg: string) {
    const safeMsg =
      getNonEmptyString(msg) ??
      (type === 'error'
        ? 'Failed to load Allegro secrets'
        : type === 'success'
          ? 'Success'
          : 'Info');

    window.dispatchEvent(
      new CustomEvent('toast', {
        detail: { type, msg: safeMsg }
      })
    );
  }

  function formatDate(timestamp: string | null) {
    if (!timestamp) return 'never';
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

  function getAccountLabel(secret: UserSecret) {
    return getNonEmptyString(secret.alias) ?? secret.short_id;
  }

  async function loadSecrets() {
    loading = true;
    networkError = null;

    try {
      vaultStatus = await userSecrets.getVaultStatus();

      if (!vaultStatus.configured || !vaultStatus.unlocked) {
        secrets = [];
        return;
      }

      const result = await allegro.listSecrets();
      secrets = result.filter((secret) => secret.type === 'allegro');
    } catch (error: unknown) {
      networkError = extractErrorMessage(error, 'Failed to load Allegro secrets');
      emitToast('error', networkError);
    } finally {
      loading = false;
    }
  }

  function openPayments(secretId: string) {
    goto(resolve(`allegro/${secretId}/payments`));
  }

  function openMatches(secretId: string) {
    goto(resolve(`allegro/${secretId}/matches`));
  }

  function openSettings() {
    goto(resolve('settings/secrets'));
  }

  onMount(() => {
    void loadSecrets();
  });
</script>

<svelte:head>
  <title>Allegro Accounts — Firefly Toolkit</title>
</svelte:head>

<div class="mx-auto flex w-full max-w-7xl min-w-0 flex-col gap-4">
  <WorkflowHeader title="Allegro accounts">
    <svelte:fragment slot="description"
      >Choose a configured Allegro secret to inspect imported payments and continue the matching
      workflow.</svelte:fragment
    >
    <svelte:fragment slot="actions"
      ><button class="btn btn-primary" on:click={openSettings}>
        <Icon src={icons.Key} class="h-5 w-5" />
        Edit secrets
      </button></svelte:fragment
    >
  </WorkflowHeader>

  <section class="card bg-base-100">
    <div class="card-body gap-4 p-4">
      <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 class="text-xl font-semibold">Configured accounts</h3>
          <p class="text-base-content/70 mt-1 text-sm">
            Review available Allegro secrets and open the payments view for a selected account.
          </p>
        </div>

        <button class="btn btn-ghost btn-sm" disabled={loading} on:click={loadSecrets}>
          {#if loading}
            <span class="loading loading-spinner loading-sm"></span>
          {:else}
            <Icon src={icons.ArrowPath} class="h-4 w-4" />
          {/if}
          Refresh
        </button>
      </div>

      <div class="divider my-0"></div>

      {#if networkError}
        <div class="alert alert-error" role="alert">
          <Icon src={icons.ExclamationTriangle} class="h-5 w-5" />
          <span>{networkError}</span>
        </div>
      {/if}

      {#if loading}
        <div class="space-y-4">
          <div class="grid gap-4 md:grid-cols-3">
            {#each Array(3) as _}
              <div class="skeleton h-24 w-full rounded-sm"></div>
            {/each}
          </div>
          <div class="space-y-2 pt-2">
            <div class="skeleton h-12 w-full rounded-sm"></div>
            <div class="skeleton h-12 w-full rounded-sm"></div>
            <div class="skeleton h-12 w-full rounded-sm"></div>
          </div>
        </div>
      {:else if vaultStatus && !vaultStatus.configured}
        <div class="bg-base-200/60 flex flex-col items-center rounded-sm px-4 py-6 text-center">
          <div class="bg-base-200 text-primary rounded-sm p-4">
            <Icon src={icons.ShieldCheck} class="h-8 w-8" />
          </div>
          <h4 class="mt-5 text-xl font-semibold">Vault is not configured</h4>
          <p class="text-base-content/70 mt-2 max-w-md text-sm">
            Configure the vault in settings before accessing Allegro accounts and imported data.
          </p>
          <button class="btn btn-primary mt-6" on:click={openSettings}>
            <Icon src={icons.Key} class="h-5 w-5" />
            Open settings
          </button>
        </div>
      {:else if vaultStatus && !vaultStatus.unlocked}
        <div class="bg-base-200/60 flex flex-col items-center rounded-sm px-4 py-6 text-center">
          <div class="bg-base-200 text-warning rounded-sm p-4">
            <Icon src={icons.LockClosed} class="h-8 w-8" />
          </div>
          <h4 class="mt-5 text-xl font-semibold">Vault is locked</h4>
          <p class="text-base-content/70 mt-2 max-w-md text-sm">
            Unlock the vault before selecting an Allegro account for payments or matches.
          </p>
          <button class="btn btn-primary mt-6" on:click={openSettings}>
            <Icon src={icons.Key} class="h-5 w-5" />
            Unlock in settings
          </button>
        </div>
      {:else if secrets.length === 0}
        <div class="bg-base-200/60 flex flex-col items-center rounded-sm px-4 py-6 text-center">
          <div class="bg-base-200 text-warning rounded-sm p-4">
            <Icon src={icons.Key} class="h-8 w-8" />
          </div>
          <h4 class="mt-5 text-xl font-semibold">No Allegro secrets yet</h4>
          <p class="text-base-content/70 mt-2 max-w-md text-sm">
            Add your first Allegro credential in settings to start reviewing imported payments.
          </p>
          <button class="btn btn-primary mt-6" on:click={openSettings}>
            <Icon src={icons.Key} class="h-5 w-5" />
            Open settings
          </button>
        </div>
      {:else}
        <div class="hidden overflow-x-auto xl:block">
          <table class="table">
            <thead>
              <tr>
                <th>Account</th>
                <th>Usage</th>
                <th>Last used</th>
                <th>Created</th>
                <th class="text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {#each secrets as secret (secret.id)}
                <tr class="hover">
                  <td>
                    <div class="flex items-center gap-3">
                      <div class="  rounded-sm px-3 py-2 text-sm font-semibold">
                        {getAccountLabel(secret)}
                      </div>
                    </div>
                  </td>
                  <td>
                    <span class="badge badge-soft badge-ghost">{secret.usage_count}</span>
                  </td>
                  <td>{formatDate(secret.last_used_at)}</td>
                  <td>{formatDate(secret.created_at)}</td>
                  <td>
                    <div class="flex justify-end gap-2">
                      <div class="tooltip" data-tip="Payments">
                        <button
                          class="btn btn-sm btn-ghost"
                          on:click={() => openPayments(secret.id)}
                        >
                          <Icon src={icons.CreditCard} class="h-4 w-4" />
                        </button>
                      </div>
                      <div class="tooltip" data-tip="Matches">
                        <button
                          class="btn btn-sm btn-ghost"
                          on:click={() => openMatches(secret.id)}
                        >
                          <Icon src={icons.Sparkles} class="h-4 w-4" />
                        </button>
                      </div>
                    </div>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>

        <div class="grid gap-4 xl:hidden">
          {#each secrets as secret (secret.id)}
            <article class="border-base-300 rounded-sm border">
              <div class="bg-base-100 rounded-sm p-5">
                <div class="flex items-start justify-between gap-4">
                  <div>
                    <div class="text-base-content/60 text-xs tracking-[0.2em] uppercase">
                      Account
                    </div>
                    <div class="mt-2 text-sm font-semibold">{getAccountLabel(secret)}</div>
                  </div>
                  <span class="badge badge-soft badge-ghost">{secret.usage_count} uses</span>
                </div>

                <div class="mt-4 grid gap-3 sm:grid-cols-2">
                  <div class="bg-base-200/60 rounded-sm px-4 py-3">
                    <div class="text-base-content/60 text-xs uppercase">Last used</div>
                    <div class="mt-1 text-sm font-medium">{formatDate(secret.last_used_at)}</div>
                  </div>
                  <div class="bg-base-200/60 rounded-sm px-4 py-3">
                    <div class="text-base-content/60 text-xs uppercase">Created</div>
                    <div class="mt-1 text-sm font-medium">{formatDate(secret.created_at)}</div>
                  </div>
                </div>

                <div class="mt-5 flex flex-wrap justify-end gap-2">
                  <button class="btn btn-sm btn-ghost" on:click={() => openMatches(secret.id)}>
                    <Icon src={icons.Sparkles} class="h-4 w-4" />
                    Open matches
                  </button>
                  <button class="btn btn-sm btn-primary" on:click={() => openPayments(secret.id)}>
                    <Icon src={icons.CreditCard} class="h-4 w-4" />
                    Open payments
                  </button>
                </div>
              </div>
            </article>
          {/each}
        </div>
      {/if}
    </div>
  </section>
</div>
