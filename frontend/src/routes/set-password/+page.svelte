<script lang="ts">
  import { afterNavigate, goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { Icon } from '@steeze-ui/svelte-icon';
  import * as icons from '@steeze-ui/heroicons';

  import { setPassword } from '#lib/api/auth.js';
  import { ApiError } from '#lib/api/errors.js';

  const REDIRECT_DELAY_MS = 2200;

  let newPassword = '';
  let confirmPassword = '';
  let submitting = false;
  let success = false;
  let formError: string | null = null;

  let token = page.url.searchParams.get('token')?.trim() ?? '';
  $: tokenMissing = token.length === 0;

  afterNavigate(() => {
    token = page.url.searchParams.get('token')?.trim() ?? '';
  });

  function getErrorMessage(error: unknown, fallback: string): string {
    if (error instanceof ApiError && (error.status === 400 || error.status === 422)) {
      return 'This invite is invalid or has expired.';
    }

    if (error instanceof Error && error.message.trim()) {
      return error.message;
    }

    return fallback;
  }

  async function submitForm() {
    formError = null;

    if (tokenMissing) {
      formError = 'Missing invite token.';
      return;
    }

    if (newPassword.length < 8) {
      formError = 'Password must be at least 8 characters long.';
      return;
    }

    if (newPassword !== confirmPassword) {
      formError = 'Passwords must match.';
      return;
    }

    submitting = true;

    try {
      await setPassword({ token, new_password: newPassword });
      success = true;
      newPassword = '';
      confirmPassword = '';

      setTimeout(() => {
        goto(resolve('login'));
      }, REDIRECT_DELAY_MS);
    } catch (error: unknown) {
      formError = getErrorMessage(error, 'Failed to set password. Please try again.');
    } finally {
      submitting = false;
    }
  }
</script>

<svelte:head>
  <title>Set Password — Firefly Toolkit</title>
</svelte:head>

<div class="bg-base-200 text-base-content flex min-h-screen items-center justify-center px-4 py-8">
  <div class="card bg-base-100 w-full max-w-lg overflow-hidden">
    <header
      class="border-base-300 flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4"
    >
      <a href={resolve('login')} class="inline-flex items-center gap-2 font-semibold"
        ><img src="/logo_b.png" alt="" class="h-6 w-6 object-contain" />Firefly Toolkit</a
      >
      <span class="text-base-content/70 text-xs">Account activation</span>
    </header>

    <section class="p-5 sm:p-6">
      <div class="mx-auto w-full">
        <div class="mb-4">
          <div class="bg-base-200 text-primary inline-flex rounded-sm p-3">
            <Icon src={icons.Key} class="h-6 w-6" />
          </div>
          <h2 class="mt-2 text-xl font-semibold tracking-tight">Set password</h2>
          <p class="text-base-content/70 mt-2 text-sm">
            Choose a strong password to complete your invite-based onboarding.
          </p>
        </div>

        {#if success}
          <div class="alert alert-success">
            <Icon src={icons.CheckCircle} class="h-5 w-5" />
            <div>
              <div class="font-semibold">Password updated</div>
              <div class="text-sm">Redirecting to login...</div>
            </div>
          </div>

          <a href={resolve('login')} class="btn btn-primary mt-6 w-full">
            <Icon src={icons.ArrowRightEndOnRectangle} class="h-5 w-5" />
            Go to login
          </a>
        {:else if tokenMissing}
          <div class="alert alert-error" role="alert">
            <Icon src={icons.ExclamationTriangle} class="h-5 w-5" />
            <div>
              <div class="font-semibold">Missing invite token</div>
              <div class="text-sm">Open this page from the invite link or append `?token=...`.</div>
            </div>
          </div>

          <a href={resolve('login')} class="btn btn-outline mt-6 w-full">Go to login</a>
        {:else}
          <form class="space-y-4" on:submit|preventDefault={submitForm}>
            <label class="form-control">
              <div class="label">
                <span class="label-text font-medium">New password</span>
              </div>
              <input
                type="password"
                class="input input-bordered w-full"
                bind:value={newPassword}
                disabled={submitting}
                placeholder="At least 8 characters"
              />
            </label>

            <label class="form-control">
              <div class="label">
                <span class="label-text font-medium">Confirm password</span>
              </div>
              <input
                type="password"
                class="input input-bordered w-full"
                bind:value={confirmPassword}
                disabled={submitting}
                placeholder="Repeat the password"
              />
            </label>

            <div class="bg-base-200/60 rounded-sm px-4 py-3 text-sm">
              Minimum length: <span class="font-semibold">8 characters</span>
            </div>

            {#if formError}
              <div class="alert alert-error" role="alert">
                <Icon src={icons.ExclamationTriangle} class="h-5 w-5" />
                <span>{formError}</span>
              </div>
            {/if}

            <button class="btn btn-primary w-full" type="submit" disabled={submitting}>
              {#if submitting}
                <span class="loading loading-spinner loading-sm"></span>
              {/if}
              Set password
            </button>
          </form>

          <a href={resolve('login')} class="btn btn-ghost mt-4 w-full">Back to login</a>
        {/if}
      </div>
    </section>
  </div>
</div>
