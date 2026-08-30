import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

function safeExternalUrl(href?: string): string | null {
  const value = href?.trim();
  if (!value || !/^(https?:|mailto:)/i.test(value)) return null;
  try {
    const parsed = new URL(value);
    return ["http:", "https:", "mailto:"].includes(parsed.protocol) ? value : null;
  } catch {
    return null;
  }
}

export default function MarkdownDocument({ name, content }: { name: string; content: string }) {
  if (!content) return <div className="file-preview-state">This document is empty.</div>;

  return <article className="file-preview-document" role="document" aria-label={`${name} document`} tabIndex={0}>
    <Markdown skipHtml
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ node: _node, href, children, ...props }) => {
          const safeHref = safeExternalUrl(href);
          return safeHref ? <a {...props} href={safeHref} target="_blank" rel="noreferrer noopener">{children}</a> : <span>{children}</span>;
        },
        img: ({ node: _node, alt }) => <span className="file-preview-omitted-media">{alt ? `Image omitted: ${alt}` : "Image omitted"}</span>,
      }}
    >{content}</Markdown>
  </article>;
}
