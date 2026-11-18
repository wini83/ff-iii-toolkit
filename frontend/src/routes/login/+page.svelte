<script lang="ts">
  import { resolve } from '$app/paths';
  import { Icon } from '@steeze-ui/svelte-icon';
  import * as icons from '@steeze-ui/heroicons';
  import type { components } from '#lib/api/schema.js';

  export let form;
  export let data: {
    systemStatus?: {
      health: components['schemas']['HealthResponse'] | null;
      version: components['schemas']['VersionResponse'] | null;
    };
  };

  let username = '';
  let password = '';
  let error: string = form?.error ?? '';

  $: if (form?.error) {
    error = form.error;
  }

  function getVersionLabel() {
    return data?.systemStatus?.version?.version ?? 'n/a';
  }

  function getStatusIcon() {
    const health = data?.systemStatus?.health;
    if (health?.status === 'ok' && health.database === 'ok') {
      return icons.ShieldCheck;
    }

    return icons.ShieldExclamation;
  }

  function getStatusTooltipText() {
    const health = data?.systemStatus?.health;
    if (!health) return 'health: n/a | database: n/a';

    return `health: ${health.status} | db: ${health.database}`;
  }
</script>

<svelte:head>
  <title>Login — Firefly Toolkit</title>
</svelte:head>

<div class="bg-base-200 text-base-content flex min-h-screen items-center justify-center px-4 py-8">
  <div class="card bg-base-100 w-full max-w-lg overflow-hidden">
    <header
      class="border-base-300 flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4"
    >
      <a href={resolve('login')} class="inline-flex items-center gap-2 font-semibold"
        ><img src="/logo_b.png" alt="" class="h-6 w-6 object-contain" />Firefly Toolkit</a
      >
      <span class="tooltip inline-flex items-center gap-2 text-xs" data-tip={getStatusTooltipText()}
        ><Icon src={getStatusIcon()} class="h-4 w-4" />v{getVersionLabel()}</span
      >
    </header>

    <section class="p-5 sm:p-6">
      <div class="mx-auto w-full">
        <div class="mb-4">
          <div class="bg-base-200 text-primary inline-flex rounded-sm p-3">
            <Icon src={icons.LockClosed} class="h-6 w-6" />
          </div>
          <h2 class="mt-2 text-xl font-semibold tracking-tight">Login</h2>
          <p class="text-base-content/70 mt-2 text-sm">
            Use your application credentials to access the toolkit dashboard.
          </p>
        </div>

        <form method="POST" class="space-y-4">
          <label class="form-control">
            <div class="label">
              <span class="label-text font-medium">Login</span>
            </div>
            <input
              id="loginUser"
              name="username"
              bind:value={username}
              placeholder="Your username"
              class="input input-bordered w-full"
              autocomplete="username"
            />
          </label>

          <label class="form-control">
            <div class="label">
              <span class="label-text font-medium">Password</span>
            </div>
            <input
              type="password"
              id="loginPass"
              name="password"
              bind:value={password}
              class="input input-bordered w-full"
              placeholder="Your password"
              autocomplete="current-password"
            />
          </label>

          <div class="bg-base-200/60 rounded-sm px-4 py-3 text-sm">
            Access is provided through administrator-created accounts.
          </div>

          {#if error}
            <div class="alert alert-error" role="alert">
              <Icon src={icons.ExclamationTriangle} class="h-5 w-5" />
              <span>{error}</span>
            </div>
          {/if}

          <button type="submit" class="btn btn-primary w-full">
            <Icon src={icons.ArrowRightEndOnRectangle} class="h-5 w-5" />
            Sign in
          </button>
        </form>
      </div>
    </section>
  </div>
</div>
