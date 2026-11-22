<script lang="ts">
  import WorkflowHeader from '#lib/components/WorkflowHeader.svelte';
  import { onMount } from 'svelte';
  import { resolve } from '$app/paths';
  import { Icon } from '@steeze-ui/svelte-icon';
  import * as icons from '@steeze-ui/heroicons';

  import { getMe } from '#lib/api/me.js';

  type MeUser = Awaited<ReturnType<typeof getMe>>;

  let loading = true;
  let pageError: string | null = null;
  let meUser: MeUser | null = null;

  function getErrorMessage(error: unknown, fallback: string): string {
    if (error instanceof Error && error.message.trim()) {
      return error.message;
    }

    return fallback;
  }

  async function loadProfile() {
    loading = true;
    pageError = null;

    try {
      meUser = await getMe();
    } catch (error: unknown) {
      pageError = getErrorMessage(error, 'Failed to load profile');
      meUser = null;
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void loadProfile();
  });
</script>

<svelte:head>
  <title>Profile — Firefly Toolkit</title>
</svelte:head>

<div class="mx-auto flex w-full max-w-7xl min-w-0 flex-col gap-4">
  <WorkflowHeader title="Profile">
    <svelte:fragment slot="description">
      Current account details returned by the application identity endpoint.
    </svelte:fragment>
    <svelte:fragment slot="actions">
      <a href={resolve('settings/secrets')} class="btn btn-ghost btn-sm">
        <Icon src={icons.Key} class="h-4 w-4" />
        Secrets
      </a>
      <button class="btn btn-ghost btn-sm" disabled={loading} on:click={loadProfile}>
        {#if loading}
          <span class="loading loading-spinner loading-sm"></span>
        {:else}
          <Icon src={icons.ArrowPath} class="h-4 w-4" />
        {/if}
        Refresh
      </button>
    </svelte:fragment>
  </WorkflowHeader>

  <section class="card bg-base-100">
    <div class="card-body gap-4 p-4">
      <div>
        <h3 class="text-xl font-semibold">Account details</h3>
        <p class="text-base-content/70 mt-1 text-sm">
          Basic identity and access information for the signed-in user.
        </p>
      </div>

      <div class="divider my-0"></div>

      {#if pageError}
        <div class="alert alert-error" role="alert">
          <Icon src={icons.ExclamationTriangle} class="h-5 w-5" />
          <span>{pageError}</span>
        </div>
      {:else if loading}
        <div class="space-y-3">
          <div class="skeleton h-24 w-full rounded-sm"></div>
          <div class="skeleton h-24 w-full rounded-sm"></div>
        </div>
      {:else if meUser}
        <div class="grid gap-4 lg:grid-cols-[0.8fr_1.2fr]">
          <article class="border-base-300 rounded-sm border">
            <div class="bg-base-100 rounded-sm p-6 text-center">
              <div class="avatar mx-auto">
                <div class="bg-base-200 w-24 rounded-sm">
                  <img
                    src={`https://api.dicebear.com/9.x/initials/svg?seed=${meUser.username}`}
                    alt="avatar"
                  />
                </div>
              </div>
              <h4 class="mt-4 text-xl font-semibold">{meUser.username}</h4>
              <p class="text-base-content/65 mt-2 text-sm">
                {meUser.is_superuser ? 'Superuser account' : 'Standard application account'}
              </p>
            </div>
          </article>

          <article class="border-base-300 rounded-sm border">
            <div class="bg-base-100 rounded-sm p-6">
              <div class="grid gap-4 sm:grid-cols-2">
                <div class="bg-base-200/60 rounded-sm px-4 py-4">
                  <div class="text-base-content/60 text-xs tracking-[0.2em] uppercase">User ID</div>
                  <div class="mt-2 font-mono text-sm">{meUser.id}</div>
                </div>

                <div class="bg-base-200/60 rounded-sm px-4 py-4">
                  <div class="text-base-content/60 text-xs tracking-[0.2em] uppercase">
                    Username
                  </div>
                  <div class="mt-2 text-sm font-medium">{meUser.username}</div>
                </div>

                <div class="bg-base-200/60 rounded-sm px-4 py-4">
                  <div class="text-base-content/60 text-xs tracking-[0.2em] uppercase">Status</div>
                  <div class="mt-2">
                    <span
                      class={`badge badge-soft ${meUser.is_active ? 'badge-success' : 'badge-error'}`}
                    >
                      {meUser.is_active ? 'Active' : 'Disabled'}
                    </span>
                  </div>
                </div>

                <div class="bg-base-200/60 rounded-sm px-4 py-4">
                  <div class="text-base-content/60 text-xs tracking-[0.2em] uppercase">Role</div>
                  <div class="mt-2">
                    <span
                      class={`badge badge-soft ${meUser.is_superuser ? 'badge-secondary' : 'badge-ghost'}`}
                    >
                      {meUser.is_superuser ? 'Superuser' : 'User'}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </article>
        </div>
      {/if}
    </div>
  </section>
</div>
