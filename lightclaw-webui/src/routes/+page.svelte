<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import ChatMessage from '$lib/components/ChatMessage.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import type { Message, Attachment, TokenStats, Tool, MemoryNote, ReadonlyStatus } from '$lib/api';
	import {
		streamChat,
		fetchModel,
		fetchHistory,
		clearHistory,
		fetchTools,
		fetchMemory,
		deleteMemory,
		setMemory,
		fetchReadonly,
	} from '$lib/api';

	// ── state ───────────────────────────────────────────────────────────────
	let messages = $state<Message[]>([]);
	let streamingMessage = $state<Message | null>(null);
	let pendingAttachments = $state<Attachment[]>([]);
	let inputText = $state('');
	let isStreaming = $state(false);

	let threadId = $state('default');
	let threads = $state<string[]>(['default']);
	let showThreadDropdown = $state(false);
	let newThreadId = $state('');

	let tokens = $state<TokenStats>({ prompt: 0, completion: 0, total: 0 });
	let contextLength = $state<number | null>(null);
	let modelName = $state('…');
	let ttft = $state<number | null>(null);

	let toolsOpen = $state(false);
	let toolsList = $state<Tool[]>([]);
	let memoryOpen = $state(false);
	let memoryList = $state<MemoryNote[]>([]);
	let newMemKey = $state('');
	let newMemVal = $state('');

	let readonlyStatus = $state<ReadonlyStatus>({ readonly: false, pid: null, thread: null });

	// ── DOM refs ─────────────────────────────────────────────────────────────
	let messagesEl = $state<HTMLElement | null>(null);
	let inputEl = $state<HTMLTextAreaElement | null>(null);
	let fileInputEl = $state<HTMLInputElement | null>(null);

	// ── init + polling ───────────────────────────────────────────────────────
	let historyTimer: ReturnType<typeof setInterval> | null = null;
	let readonlyTimer: ReturnType<typeof setInterval> | null = null;

	onMount(async () => {
		const info = await fetchModel();
		modelName = info.model;
		contextLength = info.context_length;

		const saved = localStorage.getItem('lc-threads');
		if (saved) threads = JSON.parse(saved);

		readonlyStatus = await fetchReadonly();
		await loadHistory();
		startPolling();
	});

	onDestroy(() => stopPolling());

	function startPolling() {
		if (historyTimer !== null) return;
		historyTimer = setInterval(pollHistory, 2000);
		readonlyTimer = setInterval(pollReadonly, 3000);
	}

	function stopPolling() {
		if (historyTimer !== null) {
			clearInterval(historyTimer);
			historyTimer = null;
		}
		if (readonlyTimer !== null) {
			clearInterval(readonlyTimer);
			readonlyTimer = null;
		}
	}

	async function pollReadonly() {
		try {
			readonlyStatus = await fetchReadonly();
		} catch {
			// network error — keep last known state
		}
	}

	async function pollHistory() {
		// Don't poll while streaming — we're already updating in real time
		if (isStreaming) return;
		try {
			const hist = await fetchHistory(threadId);
			if (hist.length > messages.length) {
				const atBottom =
					!messagesEl ||
					messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
				messages = hist;
				if (atBottom) {
					await tick();
					scrollBottom();
				}
			}
		} catch {
			// silently ignore network errors during polling
		}
	}

	async function loadHistory() {
		const hist = await fetchHistory(threadId);
		messages = hist;
		await tick();
		scrollBottom();
	}

	// ── thread switching ─────────────────────────────────────────────────────
	function saveThreads() {
		localStorage.setItem('lc-threads', JSON.stringify(threads));
	}

	async function switchThread(id: string) {
		threadId = id;
		showThreadDropdown = false;
		if (!threads.includes(id)) {
			threads = [...threads, id];
			saveThreads();
		}
		messages = [];
		await loadHistory();
	}

	async function createThread() {
		const id = newThreadId.trim();
		if (!id) return;
		newThreadId = '';
		await switchThread(id);
	}

	// ── send message ──────────────────────────────────────────────────────────
	async function send() {
		const text = inputText.trim();
		if ((!text && pendingAttachments.length === 0) || isStreaming) return;

		if (readonlyStatus.readonly) {
			alert(`Thread "${threadId}" is locked by the TUI (pid ${readonlyStatus.pid}). Switch to a different thread or close the TUI.`);
			return;
		}

		inputText = '';
		resizeTextarea();

		const atts = [...pendingAttachments];
		pendingAttachments = [];

		// Push user message
		messages = [...messages, { role: 'user', content: text, attachments: atts }];

		// Prepare streaming assistant message
		streamingMessage = { role: 'assistant', content: '' };
		isStreaming = true;
		const t0 = performance.now();
		let firstChunk = true;

		await tick();
		scrollBottom();

		try {
			await streamChat(text, threadId, atts, {
				onChunk(chunk) {
					if (firstChunk) {
						ttft = (performance.now() - t0) / 1000;
						firstChunk = false;
					}
					streamingMessage = {
						role: 'assistant',
						content: (streamingMessage?.content ?? '') + chunk,
					};
					scrollBottom();
				},
				onDone(stats, ctxLen) {
					tokens = stats;
					if (ctxLen !== null) contextLength = ctxLen;
				},
				onError(msg) {
					streamingMessage = { role: 'assistant', content: `**Error:** ${msg}` };
				},
			});
		} finally {
			if (streamingMessage) {
				messages = [...messages, streamingMessage];
				streamingMessage = null;
			}
			isStreaming = false;
			await tick();
			scrollBottom();
			inputEl?.focus();
		}
	}

	// ── input helpers ─────────────────────────────────────────────────────────
	function resizeTextarea() {
		if (!inputEl) return;
		inputEl.style.height = 'auto';
		inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}

	function scrollBottom() {
		if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
	}

	// ── image attach ──────────────────────────────────────────────────────────
	function addBlob(blob: Blob, mime: string) {
		const reader = new FileReader();
		reader.onload = () => {
			const dataUrl = reader.result as string;
			pendingAttachments = [
				...pendingAttachments,
				{
					data: dataUrl.split(',')[1],
					mime_type: mime,
					filename: `image.${mime.split('/')[1] || 'png'}`,
					previewUrl: dataUrl,
				},
			];
		};
		reader.readAsDataURL(blob);
	}

	function onPaste(e: ClipboardEvent) {
		for (const item of e.clipboardData?.items ?? []) {
			if (item.type.startsWith('image/')) {
				e.preventDefault();
				const blob = item.getAsFile();
				if (blob) addBlob(blob, item.type);
				break;
			}
		}
	}

	function onFileChange(e: Event) {
		const files = (e.target as HTMLInputElement).files;
		for (const f of files ?? []) {
			if (f.type.startsWith('image/')) addBlob(f, f.type);
		}
		(e.target as HTMLInputElement).value = '';
	}

	function removeAttachment(i: number) {
		pendingAttachments = pendingAttachments.filter((_, idx) => idx !== i);
	}

	// ── drag & drop ──────────────────────────────────────────────────────────
	let dragOver = $state(false);

	function onDragover(e: DragEvent) {
		e.preventDefault();
		dragOver = true;
	}

	function onDragleave() {
		dragOver = false;
	}

	function onDrop(e: DragEvent) {
		e.preventDefault();
		dragOver = false;
		for (const f of e.dataTransfer?.files ?? []) {
			if (f.type.startsWith('image/')) addBlob(f, f.type);
		}
	}

	// ── clear history ─────────────────────────────────────────────────────────
	async function doClear() {
		if (!confirm(`Clear conversation in thread "${threadId}"?`)) return;
		await clearHistory(threadId);
		messages = [];
		tokens = { prompt: 0, completion: 0, total: 0 };
		ttft = null;
	}

	// ── tools modal ───────────────────────────────────────────────────────────
	async function openTools() {
		toolsList = await fetchTools();
		toolsOpen = true;
	}

	// ── memory modal ──────────────────────────────────────────────────────────
	async function openMemory() {
		memoryList = await fetchMemory();
		memoryOpen = true;
	}

	async function addMemory() {
		const k = newMemKey.trim();
		const v = newMemVal.trim();
		if (!k || !v) return;
		await setMemory(k, v);
		newMemKey = '';
		newMemVal = '';
		memoryList = await fetchMemory();
	}

	async function removeMemory(key: string) {
		await deleteMemory(key);
		memoryList = memoryList.filter((n) => n.key !== key);
	}
</script>

<svelte:window onpaste={onPaste} />

<!-- Thread dropdown close on outside click -->
{#if showThreadDropdown}
	<div class="thread-backdrop" role="button" tabindex="-1" onclick={() => (showThreadDropdown = false)} onkeydown={(e) => e.key === 'Escape' && (showThreadDropdown = false)}></div>
{/if}

<!-- Readonly banner -->
{#if readonlyStatus.readonly}
	<div class="readonly-banner">
		Read-only — thread &ldquo;{readonlyStatus.thread}&rdquo; is active in TUI (pid {readonlyStatus.pid})
	</div>
{/if}

<!-- Header -->
<header class="header">
	<span class="logo">light-claw</span>

	<div class="thread-wrapper">
		<button
			class="btn thread-btn"
			onclick={() => (showThreadDropdown = !showThreadDropdown)}
			title="Switch thread"
		>
			{threadId} <span class="caret">▾</span>
		</button>

		{#if showThreadDropdown}
			<div class="thread-dropdown">
				<div class="dropdown-label">threads</div>
				{#each threads as t}
					<button
						class="dropdown-item"
						class:active={t === threadId}
						onclick={() => switchThread(t)}
					>
						{t}
					</button>
				{/each}
				<div class="dropdown-divider"></div>
				<div class="dropdown-new">
					<input
						class="new-thread-input"
						bind:value={newThreadId}
						placeholder="new thread…"
						onkeydown={(e) => e.key === 'Enter' && createThread()}
					/>
					<button class="btn btn-small" onclick={createThread}>go</button>
				</div>
			</div>
		{/if}
	</div>

	<div class="header-actions">
		<button class="btn" onclick={openTools} title="Registered tools">tools</button>
		<button class="btn" onclick={openMemory} title="Memory notes">memory</button>
		<button class="btn btn-danger" onclick={doClear} title="Clear history">clear</button>
	</div>
</header>

<!-- Messages -->
<main
	class="messages"
	class:drag-over={dragOver}
	bind:this={messagesEl}
	ondragover={onDragover}
	ondragleave={onDragleave}
	ondrop={onDrop}
	role="log"
	aria-live="polite"
>
	{#if messages.length === 0 && !streamingMessage}
		<div class="welcome">
			<div class="welcome-logo">&#x1f9b7;</div>
			<div class="welcome-title">light-claw</div>
			<div class="welcome-sub">local agent OS &mdash; type a message below or paste an image</div>
		</div>
	{/if}

	{#each messages as msg}
		<ChatMessage message={msg} />
	{/each}

	{#if streamingMessage}
		<ChatMessage message={streamingMessage} streaming />
	{/if}
</main>

<!-- Attachment previews -->
{#if pendingAttachments.length > 0}
	<div class="att-bar">
		{#each pendingAttachments as att, i}
			<div class="att-thumb">
				<img src={att.previewUrl} alt={att.filename} />
				<button class="att-remove" onclick={() => removeAttachment(i)} aria-label="Remove">×</button>
			</div>
		{/each}
	</div>
{/if}

<!-- Input area -->
<footer class="input-area">
	<div class="input-row">
		<button
			class="btn btn-icon"
			onclick={() => fileInputEl?.click()}
			title="Attach image (or paste with Ctrl+V)"
		>
			&#x1f4ce;
		</button>
		<textarea
			class="input-field"
			bind:value={inputText}
			bind:this={inputEl}
			placeholder="Message light-claw… (Enter to send · Shift+Enter for newline · Ctrl+V to paste image)"
			rows={1}
			disabled={isStreaming || readonlyStatus.readonly}
			onkeydown={onKeydown}
			oninput={resizeTextarea}
		></textarea>
		<button class="btn btn-send" onclick={send} disabled={isStreaming || readonlyStatus.readonly} title="Send">→</button>
	</div>

	<StatusBar {tokens} {contextLength} {ttft} model={modelName} streaming={isStreaming} />
</footer>

<input
	type="file"
	accept="image/*"
	multiple
	style="display:none"
	bind:this={fileInputEl}
	onchange={onFileChange}
/>

<!-- Tools modal -->
<Modal title="registered tools" bind:open={toolsOpen}>
	{#if toolsList.length === 0}
		<p class="empty">No tools registered.</p>
	{/if}
	{#each toolsList as tool}
		<div class="tool-row">
			<code class="tool-name">{tool.name}</code>
			<span class="tool-desc">{tool.description}</span>
		</div>
	{/each}
</Modal>

<!-- Memory modal -->
<Modal title="memory notes" bind:open={memoryOpen}>
	<div class="mem-add">
		<input class="mem-input" bind:value={newMemKey} placeholder="key" />
		<input class="mem-input mem-val" bind:value={newMemVal} placeholder="value" />
		<button class="btn btn-small" onclick={addMemory}>add</button>
	</div>
	{#if memoryList.length === 0}
		<p class="empty">No notes stored.</p>
	{/if}
	{#each memoryList as note}
		<div class="mem-row">
			<code class="mem-key">{note.key}</code>
			<span class="mem-value">{note.value}</span>
			<button class="btn btn-xs btn-danger" onclick={() => removeMemory(note.key)}>×</button>
		</div>
	{/each}
</Modal>

<style>
	:global(:root) {
		/* Catppuccin Mocha */
		--base:      #1e1e2e;
		--mantle:    #181825;
		--crust:     #11111b;
		--surface0:  #313244;
		--surface1:  #45475a;
		--surface2:  #585b70;
		--overlay0:  #6c7086;
		--overlay1:  #7f849c;
		--subtext0:  #a6adc8;
		--subtext1:  #bac2de;
		--text:      #cdd6f4;
		--cyan:      #89dceb;
		--blue:      #89b4fa;
		--green:     #a6e3a1;
		--yellow:    #f9e2af;
		--red:       #f38ba8;
		--mauve:     #cba6f7;
		--border:    #313244;

		--font-mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', ui-monospace, monospace;
		--font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
		--radius:    8px;
	}

	.readonly-banner {
	background: var(--yellow);
	color: var(--crust);
	font-family: var(--font-mono);
	font-size: 12px;
	padding: 6px 16px;
	text-align: center;
	flex-shrink: 0;
}

:global(*) {
		box-sizing: border-box;
		margin: 0;
		padding: 0;
	}

	:global(html, body) {
		height: 100%;
		background: var(--base);
		color: var(--text);
		font-family: var(--font-sans);
		font-size: 14px;
		line-height: 1.6;
	}

	:global(body) {
		display: flex;
		flex-direction: column;
		height: 100dvh;
		overflow: hidden;
	}

	:global(::-webkit-scrollbar) { width: 5px; height: 5px; }
	:global(::-webkit-scrollbar-track) { background: transparent; }
	:global(::-webkit-scrollbar-thumb) { background: var(--surface0); border-radius: 3px; }
	:global(::-webkit-scrollbar-thumb:hover) { background: var(--surface1); }

	/* hljs theme tweaks */
	:global(.hljs) { background: transparent !important; }

	/* ── header ─────────────────────────────────────────────────────────── */
	.header {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 16px;
		background: var(--mantle);
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
		min-height: 44px;
	}

	.logo {
		font-family: var(--font-mono);
		font-weight: 700;
		color: var(--cyan);
		font-size: 14px;
		letter-spacing: -0.3px;
		flex-shrink: 0;
	}

	.header-actions {
		display: flex;
		align-items: center;
		gap: 6px;
		margin-left: auto;
	}

	/* ── buttons ─────────────────────────────────────────────────────────── */
	.btn {
		background: none;
		border: 1px solid var(--border);
		color: var(--subtext0);
		padding: 4px 10px;
		border-radius: var(--radius);
		cursor: pointer;
		font-size: 12px;
		font-family: var(--font-mono);
		transition: border-color 0.12s, color 0.12s, background 0.12s;
		white-space: nowrap;
		line-height: 1.4;
	}

	.btn:hover {
		border-color: var(--cyan);
		color: var(--text);
		background: var(--surface0);
	}

	.btn:active { transform: scale(0.97); }

	.btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
		transform: none;
	}

	.btn-danger {
		border-color: transparent;
		color: var(--overlay0);
	}

	.btn-danger:hover {
		border-color: var(--red);
		color: var(--red);
		background: rgba(243, 139, 168, 0.1);
	}

	.btn-icon {
		font-size: 15px;
		padding: 4px 8px;
	}

	.btn-send {
		background: var(--cyan);
		color: var(--crust);
		border-color: var(--cyan);
		font-size: 16px;
		font-weight: 700;
		padding: 4px 14px;
		line-height: 1.6;
	}

	.btn-send:hover:not(:disabled) {
		background: var(--blue);
		border-color: var(--blue);
		color: var(--crust);
	}

	.btn-small {
		padding: 3px 8px;
		font-size: 11px;
	}

	.btn-xs {
		padding: 1px 6px;
		font-size: 11px;
	}

	/* ── thread dropdown ─────────────────────────────────────────────────── */
	.thread-wrapper {
		position: relative;
	}

	.thread-btn {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--subtext1);
	}

	.caret {
		font-size: 10px;
		color: var(--overlay0);
	}

	.thread-backdrop {
		position: fixed;
		inset: 0;
		z-index: 50;
	}

	.thread-dropdown {
		position: absolute;
		top: calc(100% + 6px);
		left: 0;
		background: var(--mantle);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		min-width: 180px;
		z-index: 100;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
		overflow: hidden;
	}

	.dropdown-label {
		font-family: var(--font-mono);
		font-size: 10px;
		color: var(--overlay0);
		padding: 8px 12px 4px;
		text-transform: uppercase;
		letter-spacing: 0.08em;
	}

	.dropdown-item {
		display: block;
		width: 100%;
		text-align: left;
		background: none;
		border: none;
		padding: 7px 12px;
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--subtext1);
		cursor: pointer;
		transition: background 0.1s;
	}

	.dropdown-item:hover { background: var(--surface0); color: var(--text); }
	.dropdown-item.active { color: var(--cyan); }

	.dropdown-divider {
		height: 1px;
		background: var(--border);
		margin: 4px 0;
	}

	.dropdown-new {
		display: flex;
		gap: 6px;
		padding: 6px 8px;
		align-items: center;
	}

	.new-thread-input {
		flex: 1;
		background: var(--surface0);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--text);
		font-family: var(--font-mono);
		font-size: 12px;
		padding: 4px 8px;
		outline: none;
	}

	.new-thread-input:focus { border-color: var(--cyan); }

	/* ── messages ────────────────────────────────────────────────────────── */
	.messages {
		flex: 1;
		overflow-y: auto;
		padding: 20px 20px;
		display: flex;
		flex-direction: column;
		gap: 18px;
	}

	.messages.drag-over {
		background: rgba(137, 220, 235, 0.04);
		outline: 2px dashed var(--cyan);
		outline-offset: -8px;
	}

	/* ── welcome ─────────────────────────────────────────────────────────── */
	.welcome {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 8px;
		flex: 1;
		opacity: 0.5;
		user-select: none;
		padding: 40px 0;
	}

	.welcome-logo {
		font-size: 2.5rem;
	}

	.welcome-title {
		font-family: var(--font-mono);
		font-size: 1.1em;
		font-weight: 700;
		color: var(--cyan);
	}

	.welcome-sub {
		font-size: 0.85em;
		color: var(--overlay1);
	}

	/* ── attachment bar ──────────────────────────────────────────────────── */
	.att-bar {
		display: flex;
		gap: 8px;
		padding: 8px 16px;
		background: var(--mantle);
		border-top: 1px solid var(--border);
		flex-wrap: wrap;
		flex-shrink: 0;
	}

	.att-thumb {
		position: relative;
	}

	.att-thumb img {
		height: 56px;
		border-radius: 6px;
		border: 1px solid var(--border);
		object-fit: cover;
		display: block;
	}

	.att-remove {
		position: absolute;
		top: -5px;
		right: -5px;
		background: var(--red);
		color: var(--crust);
		border: none;
		border-radius: 50%;
		width: 16px;
		height: 16px;
		font-size: 11px;
		cursor: pointer;
		display: flex;
		align-items: center;
		justify-content: center;
		line-height: 1;
		font-weight: 700;
	}

	/* ── input area ──────────────────────────────────────────────────────── */
	.input-area {
		padding: 10px 16px 10px;
		background: var(--mantle);
		border-top: 1px solid var(--border);
		flex-shrink: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.input-row {
		display: flex;
		align-items: flex-end;
		gap: 8px;
	}

	.input-field {
		flex: 1;
		background: var(--base);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		color: var(--text);
		font-family: var(--font-sans);
		font-size: 14px;
		padding: 9px 13px;
		resize: none;
		outline: none;
		min-height: 40px;
		max-height: 200px;
		overflow-y: auto;
		line-height: 1.5;
		transition: border-color 0.12s;
	}

	.input-field:focus { border-color: var(--cyan); }

	.input-field::placeholder { color: var(--overlay0); }

	.input-field:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	/* ── modal content ───────────────────────────────────────────────────── */
	.tool-row {
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 8px 0;
		border-bottom: 1px solid var(--surface0);
	}

	.tool-row:last-child { border-bottom: none; }

	.tool-name {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--cyan);
	}

	.tool-desc {
		font-size: 12px;
		color: var(--subtext0);
		line-height: 1.5;
	}

	.mem-add {
		display: flex;
		gap: 6px;
		margin-bottom: 12px;
		align-items: center;
	}

	.mem-input {
		background: var(--surface0);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--text);
		font-family: var(--font-mono);
		font-size: 12px;
		padding: 5px 8px;
		outline: none;
		min-width: 0;
		width: 120px;
	}

	.mem-val { flex: 1; width: auto; }

	.mem-input:focus { border-color: var(--cyan); }

	.mem-row {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		padding: 7px 0;
		border-bottom: 1px solid var(--surface0);
	}

	.mem-row:last-child { border-bottom: none; }

	.mem-key {
		font-family: var(--font-mono);
		font-size: 11px;
		color: var(--cyan);
		flex-shrink: 0;
		min-width: 80px;
	}

	.mem-value {
		flex: 1;
		font-size: 12px;
		color: var(--subtext1);
		word-break: break-word;
	}

	.empty {
		color: var(--overlay0);
		font-size: 12px;
		font-style: italic;
		padding: 8px 0;
	}
</style>
