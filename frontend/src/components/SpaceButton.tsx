"use client";

import type { ButtonHTMLAttributes, MouseEventHandler, ReactNode } from "react";
import { cn } from "@/lib/utils";

export type SpaceButtonVariant = "solid" | "outline" | "ghost";
export type SpaceButtonSize = "md" | "sm";

export type SpaceButtonProps = {
  children: ReactNode;
  href?: string;
  onClick?: MouseEventHandler<HTMLButtonElement>;
  variant?: SpaceButtonVariant;
  size?: SpaceButtonSize;
  withArrow?: boolean;
  disabled?: boolean;
  type?: ButtonHTMLAttributes<HTMLButtonElement>["type"];
  className?: string;
};

const BASE: Record<SpaceButtonVariant, string> = {
  solid: "border-white bg-white text-black hover:text-white",
  outline: "border-white/60 text-white hover:border-white hover:text-black",
  ghost: "border-transparent text-white hover:text-black",
};

const SWEEP: Record<SpaceButtonVariant, string> = {
  solid: "bg-black",
  outline: "bg-white",
  ghost: "bg-white",
};

// The site only ever needs one size. An application has toolbars and inline
// actions, so there is a compact variant too - same treatment, less padding.
const SIZE: Record<SpaceButtonSize, string> = {
  md: "px-8 py-3.5 text-xs tracking-[0.24em]",
  sm: "px-5 py-2 text-[11px] tracking-[0.2em]",
};

/**
 * The Borzo call to action: a hairline rectangle with wide uppercase
 * lettering, a fill that sweeps in from the left on hover, and an arrow that
 * steps forward. Ported from the marketing site, with `disabled` and a
 * compact size added for application use.
 */
export function SpaceButton({
  children,
  href,
  onClick,
  variant = "outline",
  size = "md",
  withArrow = true,
  disabled = false,
  type = "button",
  className,
}: SpaceButtonProps) {
  const classes = cn(
    "group/btn relative inline-flex items-center justify-center overflow-hidden border",
    "font-display uppercase transition-colors duration-500 ease-out-expo",
    "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white",
    SIZE[size],
    BASE[variant],
    // No sweep and no colour change while disabled, or a dead control still
    // invites a click.
    disabled && "pointer-events-none opacity-40",
    className,
  );

  const content = (
    <>
      <span
        aria-hidden="true"
        className={cn(
          "absolute inset-0 origin-left scale-x-0 transition-transform duration-500 ease-out-expo",
          !disabled && "group-hover/btn:scale-x-100",
          SWEEP[variant],
        )}
      />
      <span className="relative flex items-center gap-3">
        {children}
        {withArrow && (
          <svg
            viewBox="0 0 24 12"
            aria-hidden="true"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.25"
            className="h-3 w-6 transition-transform duration-500 ease-out-expo group-hover/btn:translate-x-1.5"
          >
            <path d="M0 6h21M17 1.5 21.5 6 17 10.5" />
          </svg>
        )}
      </span>
    </>
  );

  if (href) {
    return (
      <a href={href} className={classes}>
        {content}
      </a>
    );
  }

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={classes}
    >
      {content}
    </button>
  );
}
