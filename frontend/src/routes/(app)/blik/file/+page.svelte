<script lang="ts">
  import { goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import Steps from '#lib/components/Steps.svelte';

  let fileId = '';

  function goToFile() {
    const trimmed = fileId.trim();
    if (!trimmed) return; // zero jazdy bez trzymanki
    goto(resolve(`blik/file/${trimmed}`));
  }
</script>

<Steps
  activeIndex={1}
  steps={[
    { label: 'Upload', href: 'blik/upload' },
    { label: 'Preview', href: `#` },
    { label: 'Match', href: `#` }
  ]}
/>

<div class="card bg-base-100 mt-2 w-full min-w-0 p-4">
  <h2 class="text-lg font-semibold">File Preview</h2>

  <div class="divider mt-2"></div>

  <fieldset class="fieldset border-base-300 rounded-box w-full max-w-md min-w-0 border p-4">
    <legend class="fieldset-legend">Open file</legend>
    <div class="join w-full min-w-0">
      <label class="input min-w-0 flex-1">
        <span class="label">ID:</span>
        <input type="text" bind:value={fileId} placeholder="Enter ID" />
      </label>
      <button class="btn join-item" on:click={goToFile} disabled={!fileId.trim()}>Go</button>
    </div>
  </fieldset>
</div>
