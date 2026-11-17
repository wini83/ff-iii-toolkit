<script lang="ts">
  import type { Path } from '$app/types';

  type StepItem = {
    label: string;
    href: Path | '#';
  };

  export let steps: StepItem[] = [];
  export let activeIndex: number = 0;
</script>

<div class="card bg-base-100 mt-2 w-full p-4">
  <ul class="steps w-full text-xs sm:text-sm">
    {#each steps as step, i (`${step.href}-${i}`)}
      <li
        aria-current={i === activeIndex ? 'step' : undefined}
        class="step {i <= activeIndex ? 'step-primary' : ''}"
      >
        {#if step.href === '#'}
          <span>{step.label}</span>
        {:else}
          <a href={step.href} data-sveltekit-reload={step.href.startsWith('/blik/')}>
            {step.label}
          </a>
        {/if}
      </li>
    {/each}
  </ul>
</div>
