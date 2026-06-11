import { marked } from 'marked';
import hljs from 'highlight.js';

marked.use({
	gfm: true,
	breaks: true,
	renderer: {
		code({ text, lang }) {
			const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext';
			const highlighted = hljs.highlight(text, { language }).value;
			return `<pre><code class="hljs language-${language}">${highlighted}</code></pre>`;
		},
	},
});

export function renderMarkdown(text: string): string {
	return marked.parse(text) as string;
}
