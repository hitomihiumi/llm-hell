"use client";

import type { ComponentPropsWithoutRef, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { splitCitations } from "@/components/CitedText";
import type { Citation } from "@/lib/types";

/**
 * Renders a model answer as Markdown, in the Borzo type system, with `[n]`
 * still clickable.
 *
 * Why Markdown and not MDX, given the site renders its blog with
 * `compileMDX`. Two reasons, and both are about *this* content rather than a
 * preference:
 *
 *   1. `next-mdx-remote/rsc` is an async Server Component. The answer arrives
 *      token by token into a client component, so there is no server render
 *      to compile it in - the text does not exist yet when the page renders.
 *   2. MDX *evaluates* what it parses. A model writing `{"a": 1}` in an
 *      answer is writing a JSX expression as far as MDX is concerned, and a
 *      stray `<Foo>` is a component reference. Compiling model output would
 *      turn an ordinary answer containing JSON into a crash. Markdown has no
 *      evaluation step, and react-markdown escapes raw HTML unless you opt in
 *      (we do not), which is the right posture for text we did not write.
 *
 * The component map below is the site's `MdxContent` styling, so an answer
 * and a blog post set the same way.
 */

type Renderer<T extends keyof React.JSX.IntrinsicElements> = (
  props: ComponentPropsWithoutRef<T>,
) => ReactNode;

export function AnswerMarkdown({
  text,
  citations,
  onJump,
}: {
  text: string;
  citations: Citation[];
  onJump: (citation: Citation) => void;
}) {
  /**
   * Turn `[n]` inside already-parsed content into buttons.
   *
   * This runs on the tree rather than on the raw string, because by the time
   * Markdown has been parsed the citation may sit inside a list item, a table
   * cell or a bold run. Only *string* children are touched: a nested element
   * is left alone and handled by its own renderer, so nothing is processed
   * twice. `code` and `pre` deliberately have no override - a `[0]` inside a
   * code sample is an array index, not a source.
   */
  const cite = (children: ReactNode): ReactNode => {
    if (typeof children === "string")
      return splitCitations(children, citations, onJump);
    if (Array.isArray(children)) {
      return children.map((child, index) =>
        typeof child === "string" ? (
          // biome-ignore lint/suspicious/noArrayIndexKey: positional children of a parsed node, stable for a given render
          <span key={index}>
            {splitCitations(child, citations, onJump, `c${index}`)}
          </span>
        ) : (
          child
        ),
      );
    }
    return children;
  };

  const components = {
    h1: (({ children, ...props }) => (
      <h2
        className="mt-8 mb-3 font-display text-2xl font-semibold uppercase tracking-tight text-white first:mt-0"
        {...props}
      >
        {cite(children)}
      </h2>
    )) as Renderer<"h1">,

    h2: (({ children, ...props }) => (
      <h2
        className="mt-8 mb-3 font-display text-2xl font-semibold uppercase tracking-tight text-white first:mt-0"
        {...props}
      >
        {cite(children)}
      </h2>
    )) as Renderer<"h2">,

    h3: (({ children, ...props }) => (
      <h3
        className="mt-6 mb-2 font-display text-lg font-semibold uppercase tracking-tight text-white"
        {...props}
      >
        {cite(children)}
      </h3>
    )) as Renderer<"h3">,

    p: (({ children, ...props }) => (
      <p
        className="mb-4 text-[15px] leading-[1.75] text-white/85 last:mb-0"
        {...props}
      >
        {cite(children)}
      </p>
    )) as Renderer<"p">,

    // The site's list mark: a short rule rather than a bullet.
    ul: (({ children, ...props }) => (
      <ul
        className="mb-4 list-none space-y-2 text-[15px] text-white/85"
        {...props}
      >
        {children}
      </ul>
    )) as Renderer<"ul">,

    ol: (({ children, ...props }) => (
      <ol
        className="mb-4 list-decimal space-y-2 pl-5 text-[15px] text-white/85 marker:font-mono marker:text-xs marker:text-white/35"
        {...props}
      >
        {children}
      </ol>
    )) as Renderer<"ol">,

    li: (({ children, ...props }) => (
      <li
        className="leading-[1.75] before:mt-3 before:mr-3 before:inline-block before:h-px before:w-3 before:shrink-0 before:bg-white/40 before:align-top"
        {...props}
      >
        {cite(children)}
      </li>
    )) as Renderer<"li">,

    strong: (({ children, ...props }) => (
      <strong className="font-semibold text-white" {...props}>
        {cite(children)}
      </strong>
    )) as Renderer<"strong">,

    em: (({ children, ...props }) => (
      <em className="italic" {...props}>
        {cite(children)}
      </em>
    )) as Renderer<"em">,

    blockquote: (({ children, ...props }) => (
      <blockquote
        className="my-5 border-l border-hairline pl-5 text-white/60 italic"
        {...props}
      >
        {children}
      </blockquote>
    )) as Renderer<"blockquote">,

    a: (({ children, ...props }) => (
      <a
        className="text-accent underline decoration-accent/40 underline-offset-4 transition-colors duration-300 hover:decoration-accent"
        target="_blank"
        rel="noreferrer noopener"
        {...props}
      >
        {children}
      </a>
    )) as Renderer<"a">,

    // No citation pass here on purpose: a bracketed number inside code is
    // part of the code.
    code: (({ children, className, ...props }) => {
      const fenced = /language-/.test(className ?? "");
      return fenced ? (
        <code
          className="font-mono text-[12px] leading-relaxed text-white/80"
          {...props}
        >
          {children}
        </code>
      ) : (
        <code
          className="border border-hairline px-1 py-0.5 font-mono text-[0.85em] text-accent"
          {...props}
        >
          {children}
        </code>
      );
    }) as Renderer<"code">,

    pre: (({ children, ...props }) => (
      <pre
        className="mb-4 overflow-x-auto border border-hairline p-4 font-mono text-[12px] leading-relaxed text-white/80"
        {...props}
      >
        {children}
      </pre>
    )) as Renderer<"pre">,

    hr: (() => (
      <hr className="my-6 border-0 border-t border-hairline" />
    )) as Renderer<"hr">,

    // GFM tables. Wrapped so a wide table scrolls inside itself instead of
    // making the whole answer panel scroll sideways.
    table: (({ children, ...props }) => (
      <div className="mb-4 overflow-x-auto">
        <table className="w-full border-collapse text-sm" {...props}>
          {children}
        </table>
      </div>
    )) as Renderer<"table">,

    th: (({ children, ...props }) => (
      <th
        className="border-b border-hairline py-2 pr-4 text-left font-display text-[11px] uppercase tracking-[0.24em] font-medium text-white/40"
        {...props}
      >
        {cite(children)}
      </th>
    )) as Renderer<"th">,

    td: (({ children, ...props }) => (
      <td
        className="border-b border-hairline py-2 pr-4 align-top text-white/75"
        {...props}
      >
        {cite(children)}
      </td>
    )) as Renderer<"td">,
  };

  return (
    <div className="answer-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
