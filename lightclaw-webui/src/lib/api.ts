export interface Attachment {
	data: string; // base64
	mime_type: string;
	filename: string;
	previewUrl: string; // local blob URL, not sent to server
}

export interface Message {
	role: 'user' | 'assistant';
	content: string;
	attachments?: Attachment[];
}

export interface TokenStats {
	prompt: number;
	completion: number;
	total: number;
}

export interface ModelInfo {
	model: string;
	base_url: string;
	context_length: number | null;
}

export interface MemoryNote {
	key: string;
	value: string;
	tags: string[];
}

export interface Tool {
	name: string;
	description: string;
}

export async function fetchModel(): Promise<ModelInfo> {
	const r = await fetch('/api/model');
	return r.json();
}

export async function fetchHistory(threadId: string): Promise<Message[]> {
	const r = await fetch(`/api/history/${encodeURIComponent(threadId)}`);
	const data = await r.json();
	return data.messages ?? [];
}

export async function clearHistory(threadId: string): Promise<void> {
	await fetch(`/api/history/${encodeURIComponent(threadId)}`, { method: 'DELETE' });
}

export async function fetchTools(): Promise<Tool[]> {
	const r = await fetch('/api/tools');
	const data = await r.json();
	return data.tools ?? [];
}

export async function fetchMemory(): Promise<MemoryNote[]> {
	const r = await fetch('/api/memory');
	const data = await r.json();
	return data.notes ?? [];
}

export async function setMemory(key: string, value: string): Promise<void> {
	await fetch('/api/memory', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ key, value }),
	});
}

export async function deleteMemory(key: string): Promise<void> {
	await fetch(`/api/memory/${encodeURIComponent(key)}`, { method: 'DELETE' });
}

export interface ChatChunkCallback {
	onChunk: (text: string) => void;
	onDone: (stats: TokenStats, contextLength: number | null) => void;
	onError: (msg: string) => void;
}

export interface ReadonlyStatus {
	readonly: boolean;
	pid: number | null;
	thread: string | null;
}

export async function fetchReadonly(): Promise<ReadonlyStatus> {
	const r = await fetch('/api/readonly');
	return r.json();
}

export async function streamChat(
	message: string,
	threadId: string,
	attachments: Attachment[],
	cb: ChatChunkCallback
): Promise<void> {
	const response = await fetch('/api/chat', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
			message,
			thread_id: threadId,
			attachments: attachments.map(({ data, mime_type, filename }) => ({
				type: 'image',
				data,
				mime_type,
				filename,
			})),
		}),
	});

	if (!response.ok) {
		cb.onError(`HTTP ${response.status}: ${response.statusText}`);
		return;
	}

	const reader = response.body!.getReader();
	const decoder = new TextDecoder();
	let buf = '';

	while (true) {
		const { done, value } = await reader.read();
		if (done) break;

		buf += decoder.decode(value, { stream: true });
		const lines = buf.split('\n');
		buf = lines.pop() ?? '';

		for (const line of lines) {
			if (!line.startsWith('data: ')) continue;
			try {
				const payload = JSON.parse(line.slice(6));
				if (payload.chunk !== undefined) {
					cb.onChunk(payload.chunk);
				} else if (payload.done) {
					cb.onDone(payload.tokens ?? { prompt: 0, completion: 0, total: 0 }, payload.context_length ?? null);
				} else if (payload.error) {
					cb.onError(payload.error);
				}
			} catch {
				// malformed SSE line
			}
		}
	}
}
