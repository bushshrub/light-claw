<script lang="ts">
	import type { Message } from '$lib/api';
	import { renderMarkdown } from '$lib/markdown';

	let { message, streaming = false }: { message: Message; streaming?: boolean } = $props();
</script>

<div class="message message-{message.role}" class:streaming>
	{#if message.role === 'user'}
		<div class="user-bubble">
			{#if message.attachments?.length}
				<div class="attachments">
					{#each message.attachments as att}
						<img src={att.previewUrl} alt={att.filename} class="att-img" />
					{/each}
				</div>
			{/if}
			<p class="user-text">{message.content}</p>
		</div>
	{:else}
		<div class="assistant-content prose">
			<!-- eslint-disable-next-line svelte/no-at-html-tags -->
			{@html renderMarkdown(message.content)}
			{#if streaming}<span class="cursor">▋</span>{/if}
		</div>
	{/if}
</div>

<style>
	.message {
		display: flex;
		animation: fadeIn 0.12s ease;
	}

	@keyframes fadeIn {
		from { opacity: 0; transform: translateY(3px); }
		to   { opacity: 1; transform: translateY(0); }
	}

	.message-user {
		justify-content: flex-end;
	}

	.message-assistant {
		justify-content: flex-start;
	}

	.user-bubble {
		background: var(--surface1);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 10px 14px;
		max-width: 68%;
	}

	.user-text {
		margin: 0;
		white-space: pre-wrap;
		word-break: break-word;
		color: var(--text);
		line-height: 1.6;
	}

	.attachments {
		display: flex;
		gap: 6px;
		margin-bottom: 8px;
		flex-wrap: wrap;
	}

	.att-img {
		max-height: 160px;
		max-width: 260px;
		border-radius: 6px;
		border: 1px solid var(--border);
		object-fit: cover;
		display: block;
	}

	.assistant-content {
		max-width: 100%;
		min-height: 1.5em;
		color: var(--text);
		line-height: 1.75;
	}

	.cursor {
		display: inline-block;
		animation: blink 0.7s step-end infinite;
		color: var(--cyan);
		font-weight: bold;
		margin-left: 1px;
	}

	@keyframes blink {
		0%, 100% { opacity: 1; }
		50% { opacity: 0; }
	}

	/* ── prose (markdown) ── */
	.prose :global(h1),
	.prose :global(h2),
	.prose :global(h3),
	.prose :global(h4) {
		color: var(--cyan);
		font-family: var(--font-mono);
		margin: 1.1em 0 0.4em;
	}

	.prose :global(h1) { font-size: 1.35em; }
	.prose :global(h2) { font-size: 1.15em; }
	.prose :global(h3) { font-size: 1.0em; }

	.prose :global(p) {
		margin: 0.55em 0;
	}

	.prose :global(code) {
		background: var(--surface0);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 1px 5px;
		font-family: var(--font-mono);
		font-size: 0.87em;
		color: var(--mauve);
	}

	.prose :global(pre) {
		background: var(--surface0);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 14px 16px;
		overflow-x: auto;
		margin: 0.8em 0;
	}

	.prose :global(pre code) {
		background: none;
		border: none;
		padding: 0;
		color: inherit;
		font-size: 0.875em;
	}

	.prose :global(ul),
	.prose :global(ol) {
		padding-left: 1.4em;
		margin: 0.5em 0;
	}

	.prose :global(li) {
		margin: 0.25em 0;
	}

	.prose :global(blockquote) {
		border-left: 3px solid var(--cyan);
		margin: 0.8em 0;
		padding: 4px 12px;
		color: var(--subtext1);
		background: var(--surface0);
		border-radius: 0 var(--radius) var(--radius) 0;
	}

	.prose :global(a) {
		color: var(--blue);
		text-decoration: none;
	}

	.prose :global(a:hover) {
		text-decoration: underline;
	}

	.prose :global(table) {
		border-collapse: collapse;
		margin: 0.8em 0;
		width: 100%;
		font-size: 0.9em;
	}

	.prose :global(th),
	.prose :global(td) {
		border: 1px solid var(--border);
		padding: 6px 10px;
		text-align: left;
	}

	.prose :global(th) {
		background: var(--surface1);
		color: var(--cyan);
		font-family: var(--font-mono);
	}

	.prose :global(tr:nth-child(even)) {
		background: var(--surface0);
	}

	.prose :global(strong) {
		color: var(--yellow);
		font-weight: 600;
	}

	.prose :global(em) {
		color: var(--subtext1);
	}

	.prose :global(hr) {
		border: none;
		border-top: 1px solid var(--border);
		margin: 1em 0;
	}

	.prose :global(img) {
		max-width: 100%;
		border-radius: var(--radius);
		border: 1px solid var(--border);
	}
</style>
