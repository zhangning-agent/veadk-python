import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function IconFrame({
  size = 18,
  children,
  ...props
}: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export function SandboxWebIcon(props: IconProps) {
  return (
    <IconFrame {...props}>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M3.9 12h16.2M12 3.75c2.15 2.25 3.25 5 3.25 8.25S14.15 18 12 20.25C9.85 18 8.75 15.25 8.75 12S9.85 6 12 3.75Z" />
    </IconFrame>
  );
}

export function SandboxTerminalIcon(props: IconProps) {
  return (
    <IconFrame {...props}>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
      <path d="m7.25 9 2.75 2.5L7.25 14M12.5 14h4.25" />
    </IconFrame>
  );
}

export function SandboxExternalLinkIcon(props: IconProps) {
  return (
    <IconFrame {...props}>
      <path d="M13 5h6v6M19 5l-8.25 8.25" />
      <path d="M18 14.5V18a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18V7.5A1.5 1.5 0 0 1 6 6h3.5" />
    </IconFrame>
  );
}

export function SandboxMinimizeIcon(props: IconProps) {
  return (
    <IconFrame {...props}>
      <path d="M5 12h14" />
    </IconFrame>
  );
}

export function SandboxCloseIcon(props: IconProps) {
  return (
    <IconFrame {...props}>
      <path d="m6.5 6.5 11 11m0-11-11 11" />
    </IconFrame>
  );
}

export function SandboxOpenIcon(props: IconProps) {
  return (
    <IconFrame {...props}>
      <path d="M8 4.5H4.5V8M16 4.5h3.5V8M8 19.5H4.5V16M16 19.5h3.5V16" />
    </IconFrame>
  );
}

export function SandboxLoadingIcon(props: IconProps) {
  return (
    <IconFrame {...props}>
      <path d="M20 12a8 8 0 1 1-2.35-5.65" />
    </IconFrame>
  );
}
