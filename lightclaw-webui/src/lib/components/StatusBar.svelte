<script lang="ts">
	import type { TokenStats } from '$lib/api';

	let {
		tokens,
		contextLength,
		ttft,
		model,
		streaming = false,
	}: {
		tokens: TokenStats;
		contextLength: number | null;
		ttft: number | null;
		model: string;
		streaming?: boolean;
	} = $props();

	const ctx = $derived(contextLength ?? 128_000);
	const pct = $derived(Math.min((tokens.total / ctx) * 100, 100));
	const filled = $derived(Math.round((pct / 100) * 20));
	const bar = $derived('█'.repeat(filled) + '░'.repeat(20 - filled));
	const barColor = $derived(pct < 50 ? 'var(--green)' : pct < 80 ? 'var(--yellow)' : 'var(--red)');

	function fmt(n: number): string {
		return n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);
	}
</script>

<div class="status-bar" class:streaming>
	<span class="tok-count">{fmt(tokens.total)}/{fmt(ctx)}</span>
	<span class="tok-bar" style="color:{barColor}">{bar}</span>
	{#if ttft !== null}
		<span class="dim">ttft {ttft.toFixed(2)}s</span>
	{/if}
	{#if tokens.completion > 0}
		<span class="dim">↳ {fmt(tokens.completion)} tok</span>
	{/if}
	{#if streaming}
		<span class="streaming-dot">generating…</span>
	{/if}
	<span class="model">{model}</span>
</div>

<style>
	.status-bar {
		display: flex;
		align-items: center;
		gap: 10px;
		font-family: var(--font-mono);
		font-size: 11px;
		color: var(--subtext0);
		height: 18px;
		line-height: 1;
		overflow: hidden;
	}

	.tok-count {
		color: var(--subtext1);
		min-width: 6em;
	}

	.tok-bar {
		letter-spacing: -1.2px;
		flex-shrink: 0;
	}

	.dim {
		color: var(--overlay0);
	}

	.streaming-dot {
		color: var(--cyan);
		animation: pulse 1s ease-in-out infinite;
	}

	@keyframes pulse {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.4; }
	}

	.model {
		margin-left: auto;
		color: var(--overlay0);
		font-size: 10px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		max-width: 24ch;
	}
</style>
