<script lang="ts">
	let {
		title,
		open = $bindable(false),
		children,
	}: {
		title: string;
		open?: boolean;
		children: import('svelte').Snippet;
	} = $props();

	function close() {
		open = false;
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') close();
	}
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
	<div class="overlay" role="dialog" aria-modal="true">
		<div class="backdrop" role="button" tabindex="-1" onclick={close} onkeydown={(e) => e.key === 'Escape' && close()}></div>
		<div class="box">
			<div class="header">
				<span class="title">{title}</span>
				<button class="close-btn" onclick={close} aria-label="Close">✕</button>
			</div>
			<div class="body">
				{@render children()}
			</div>
		</div>
	</div>
{/if}

<style>
	.overlay {
		position: fixed;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 200;
		padding: 20px;
	}

	.backdrop {
		position: absolute;
		inset: 0;
		background: rgba(0, 0, 0, 0.6);
		backdrop-filter: blur(2px);
	}

	.box {
		position: relative;
		background: var(--base);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		width: 100%;
		max-width: 620px;
		max-height: 72vh;
		display: flex;
		flex-direction: column;
		box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
	}

	.header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 12px 16px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.title {
		font-family: var(--font-mono);
		font-weight: 700;
		font-size: 13px;
		color: var(--cyan);
	}

	.close-btn {
		background: none;
		border: none;
		color: var(--subtext0);
		cursor: pointer;
		font-size: 14px;
		padding: 2px 6px;
		border-radius: 4px;
		line-height: 1;
	}

	.close-btn:hover {
		background: var(--surface1);
		color: var(--text);
	}

	.body {
		overflow-y: auto;
		padding: 12px 16px;
		flex: 1;
	}
</style>
