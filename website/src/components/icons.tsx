type IconProps = { className?: string };

const line = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

export function AppleIcon({ className = "size-[1.05em]" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
      <path d="M16.37 12.6c-.02-2.2 1.8-3.26 1.88-3.31-1.03-1.5-2.62-1.7-3.18-1.73-1.35-.14-2.64.8-3.33.8-.69 0-1.74-.78-2.86-.76-1.47.02-2.83.86-3.59 2.17-1.53 2.66-.39 6.6 1.1 8.75.73 1.05 1.6 2.24 2.73 2.2 1.1-.05 1.51-.71 2.84-.71 1.32 0 1.7.71 2.86.69 1.18-.02 1.93-1.07 2.65-2.13.84-1.22 1.18-2.4 1.2-2.46-.03-.01-2.3-.88-2.32-3.5zM14.2 6.13c.6-.73 1.01-1.75.9-2.76-.87.04-1.92.58-2.54 1.31-.56.65-1.05 1.68-.92 2.68.97.07 1.96-.5 2.56-1.23z" />
    </svg>
  );
}

export function GitHubIcon({ className = "size-[1.05em]" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
      <path d="M12 2C6.48 2 2 6.58 2 12.23c0 4.52 2.87 8.35 6.84 9.7.5.1.68-.22.68-.49l-.01-1.7c-2.78.62-3.37-1.37-3.37-1.37-.46-1.19-1.11-1.5-1.11-1.5-.91-.64.07-.62.07-.62 1 .07 1.53 1.06 1.53 1.06.9 1.57 2.35 1.12 2.92.85.09-.66.35-1.12.64-1.37-2.22-.26-4.56-1.14-4.56-5.07 0-1.12.39-2.04 1.03-2.76-.1-.26-.45-1.3.1-2.71 0 0 .84-.28 2.75 1.05a9.3 9.3 0 0 1 5 0c1.91-1.33 2.75-1.05 2.75-1.05.55 1.41.2 2.45.1 2.71.64.72 1.03 1.64 1.03 2.76 0 3.94-2.34 4.81-4.57 5.06.36.32.68.94.68 1.9l-.01 2.82c0 .27.18.6.69.49A10.1 10.1 0 0 0 22 12.23C22 6.58 17.52 2 12 2z" />
    </svg>
  );
}

export function ReplayIcon({ className = "size-[1.05em]" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} {...line} aria-hidden>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
    </svg>
  );
}

export function SandboxIcon({ className = "size-5" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} {...line} aria-hidden>
      <path d="M12 3l7.5 3v5.5c0 4.6-3.1 8-7.5 9.5-4.4-1.5-7.5-4.9-7.5-9.5V6z" />
      <path d="M9.5 12.5l2 2 3.5-4" />
    </svg>
  );
}

export function KeyIcon({ className = "size-5" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} {...line} aria-hidden>
      <circle cx="8" cy="15" r="4" />
      <path d="M11 12l8.5-8.5M16.5 7l2.5 2.5M14.5 9l2 2" />
    </svg>
  );
}

export function BranchIcon({ className = "size-5" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} {...line} aria-hidden>
      <circle cx="7" cy="5.5" r="2" />
      <circle cx="7" cy="18.5" r="2" />
      <circle cx="17" cy="8.5" r="2" />
      <path d="M7 7.5v9M17 10.5c0 4-10 2.5-10 6" />
    </svg>
  );
}

export function FlaskIcon({ className = "size-5" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} {...line} aria-hidden>
      <path d="M9.5 3.5h5M10.5 3.5v6L5.2 18.6A1.6 1.6 0 0 0 6.6 21h10.8a1.6 1.6 0 0 0 1.4-2.4L13.5 9.5v-6" />
      <path d="M7.8 15h8.4" />
    </svg>
  );
}

export function StarIcon({ className = "size-5" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} {...line} aria-hidden>
      <path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 16.9l-5.2 2.8 1-5.9-4.3-4.1 5.9-.8z" />
    </svg>
  );
}

export function MegaphoneIcon({ className = "size-5" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} {...line} aria-hidden>
      <path d="M4 10v4h3l8 4.5v-13L7 10z" />
      <path d="M18.5 9.5a3.5 3.5 0 0 1 0 5M7 14l1 5h2.5l-1-4.4" />
    </svg>
  );
}
