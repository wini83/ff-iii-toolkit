<script lang="ts">
  import { onMount } from 'svelte';
  import {
    Chart,
    BarController,
    BarElement,
    CategoryScale,
    LinearScale,
    Tooltip,
    Legend
  } from 'chart.js';
  Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip, Legend);
  export let title: string;
  export let labels: string[] = [];
  export let values: (number | null)[] = [];
  export let currency = '';
  export let loading = false;
  export let onMonth: ((month: string) => void) | undefined = undefined;
  let canvas: HTMLCanvasElement;
  const state = { chart: null as Chart<'bar', (number | null)[], string> | null };
  let mounted = false;

  function render(dependencies = { title, labels, values, currency, onMonth }) {
    const { title, labels, values, currency, onMonth } = dependencies;
    const chart = state.chart;
    if (!chart) return;
    const styles = getComputedStyle(document.documentElement);
    const color = (name: string) => styles.getPropertyValue(`--color-${name}`).trim();
    chart.data = {
      labels,
      datasets: [
        {
          label: currency ? `${title} (${currency})` : `${title} (transactions)`,
          data: values,
          backgroundColor: color('primary')
        }
      ]
    };
    chart.options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      color: color('base-content'),
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: color('base-100'),
          titleColor: color('base-content'),
          bodyColor: color('base-content'),
          borderColor: color('base-300'),
          borderWidth: 1,
          callbacks: {
            label: (context) =>
              currency
                ? `${context.formattedValue} ${currency}`
                : `${context.formattedValue} transactions`
          }
        }
      },
      scales: {
        x: {
          ticks: { color: color('base-content'), maxRotation: 45, minRotation: 0 },
          grid: { display: false },
          border: { color: color('base-300') }
        },
        y: {
          beginAtZero: true,
          ticks: { color: color('base-content'), ...(currency ? {} : { precision: 0 }) },
          grid: { color: color('base-300') },
          border: { display: false },
          title: { display: true, text: currency || 'Transactions', color: color('base-content') }
        }
      },
      onClick: (_event, elements) => {
        if (elements.length) onMonth?.(labels[elements[0].index]);
      }
    };
    chart.update();
  }
  $: if (mounted) render({ title, labels, values, currency, onMonth });

  onMount(() => {
    state.chart = new Chart(canvas, { type: 'bar', data: { labels: [], datasets: [] } });
    mounted = true;
    render();
    const observer = new MutationObserver(() => render());
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme']
    });
    return () => {
      observer.disconnect();
      state.chart?.destroy();
      state.chart = null;
      mounted = false;
    };
  });
</script>

<section class="card bg-base-100 min-w-0 p-4" aria-label={title}>
  <h3 class="mb-3 font-semibold">{title}{currency ? ` · ${currency}` : ''}</h3>
  {#if loading}<p class="text-base-content/70 text-sm" role="status">Loading chart…</p>
  {:else if !values.some((value) => value !== null)}<p class="text-base-content/70 text-sm">
      No monthly data{currency ? ` for ${currency}` : ''}.
    </p>{/if}
  <div
    class="relative h-64 min-w-0"
    class:hidden={loading || !values.some((value) => value !== null)}
  >
    <canvas
      bind:this={canvas}
      aria-label={`${title}${currency ? ` in ${currency}` : ' in transactions'}`}
    >
      {labels.map((month, index) => `${month}: ${values[index] ?? 'no data'}`).join('; ')}
    </canvas>
  </div>
</section>
