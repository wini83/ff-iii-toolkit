<script lang="ts">
  export let title: string;
</script>

<header
  class="border-base-300 flex min-w-0 flex-wrap items-start justify-between gap-3 border-b pb-3"
>
  <div class="min-w-0 flex-1 basis-64">
    <h2 class="text-lg font-semibold [overflow-wrap:anywhere]">{title}</h2>
    <div class="text-base-content/70 mt-1 text-sm [overflow-wrap:anywhere]">
      <slot name="description" />
    </div>
  </div>
  <div class="flex max-w-full flex-wrap items-center gap-2"><slot name="actions" /></div>
</header>
