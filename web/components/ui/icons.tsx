// Tiny inline icon set — stroke-based, inherits currentColor, no icon library needed.

function base(props: React.SVGProps<SVGSVGElement>) {
  return {
    width: 16,
    height: 16,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    ...props,
  };
}

export function ChevronDown(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M4 6l4 4 4-4" />
    </svg>
  );
}

export function Check(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M3 8.5l3.2 3.2L13 5" />
    </svg>
  );
}

export function Search(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L14 14" />
    </svg>
  );
}

export function Plus(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M8 3.5v9M3.5 8h9" />
    </svg>
  );
}

export function X(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  );
}

export function ArrowRight(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M3 8h10M9 4l4 4-4 4" />
    </svg>
  );
}

export function Refresh(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" />
      <path d="M13.5 2.5v2.6h-2.6" />
    </svg>
  );
}

// ── 24-grid line icons for the "Why AEO Studio" trust cards (design handoff §4) ──

export function ChartUp(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base({ viewBox: "0 0 24 24", width: 20, height: 20, ...props })}>
      <path d="M3 3v18h18" />
      <path d="M6 15l4.5-5.5 3.5 3L19 6" />
    </svg>
  );
}

export function ShieldCheck(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base({ viewBox: "0 0 24 24", width: 20, height: 20, ...props })}>
      <path d="M12 3l7 3v5c0 4.8-3.2 7.9-7 10-3.8-2.1-7-5.2-7-10V6z" />
      <path d="M9.3 12l2 2 3.6-4.2" />
    </svg>
  );
}

export function DocLines(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base({ viewBox: "0 0 24 24", width: 20, height: 20, ...props })}>
      <rect x="4" y="3" width="16" height="18" rx="2.5" />
      <path d="M8.5 8.5h7" />
      <path d="M8.5 12.5h7" />
      <path d="M8.5 16.5h4" />
    </svg>
  );
}

export function Sparkle(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...base(props)}>
      <path d="M8 2.5c.5 2.7 1.8 4 4.5 4.5-2.7.5-4 1.8-4.5 4.5-.5-2.7-1.8-4-4.5-4.5 2.7-.5 4-1.8 4.5-4.5z" />
      <path d="M12.8 10.8c.25 1.35.9 2 2.2 2.2-1.3.25-1.95.9-2.2 2.2-.25-1.3-.9-1.95-2.2-2.2 1.3-.25 1.95-.85 2.2-2.2z" />
    </svg>
  );
}
