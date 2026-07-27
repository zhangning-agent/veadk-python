import hermesLogo from "../assets/hermes.svg";
import openClawLogo from "../assets/openclaw.svg";
import "./SandboxBrandIcon.css";

export type SandboxBrand = "openclaw" | "hermes" | "code";

export function SandboxBrandIcon({
  brand,
  className = "",
}: {
  brand: SandboxBrand;
  className?: string;
}) {
  const label =
    brand === "openclaw" ? "OpenClaw" : brand === "hermes" ? "Hermes" : "Code 沙箱";
  const classes = `sandbox-brand-icon sandbox-brand-icon--${brand}${className ? ` ${className}` : ""}`;
  if (brand === "code") {
    return (
      <svg
        className={classes}
        viewBox="0 0 32 32"
        role="img"
        aria-label={label}
      >
        <path
          className="sandbox-brand-icon__code-back"
          d="M2.9 25.8 8.3 9.9a1 1 0 0 1 1.9 0l5.3 15.9H2.9Z"
        />
        <path
          className="sandbox-brand-icon__code-main"
          d="M9.2 25.8 16.2 4a1 1 0 0 1 1.9 0l7 21.8H9.2Z"
        />
        <path
          className="sandbox-brand-icon__code-side"
          d="m19.6 25.8 3.7-11.2a1 1 0 0 1 1.9 0l3.8 11.2h-9.4Z"
        />
        <path
          className="sandbox-brand-icon__code-prompt"
          d="m12.7 18 2.5 2.1-2.5 2.1M17.2 22.2h3.2"
        />
      </svg>
    );
  }
  return (
    <img
      className={classes}
      src={brand === "openclaw" ? openClawLogo : hermesLogo}
      alt=""
      aria-hidden="true"
      title={`${label} logo`}
    />
  );
}
